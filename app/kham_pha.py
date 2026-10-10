"""Khám phá module từ CẤU TRÚC thư mục — không từ danh sách gõ tay (KE-HOACH-MODULE.md mục 8).

Thêm module = có một thư mục `<id>/` chứa `manifest.json` + `module.py` ở MỘT trong hai nguồn:
  - `app/modules/*/`             đi kèm ảnh, chỉ-đọc (hiện rỗng — lõi nhẹ);
  - `<WEB2API_DATA>/modules/*/`  cài lúc chạy (từ git), ghi được.
Không sửa dòng nào của lõi. Bảng khai riêng cho từng module là "thêm một danh sách nữa" —
hỏng đúng kiểu `_PREFETCH_MAP` của c2a (biết 3/109 công cụ).

HỢP ĐỒNG lớp `Module` trong module.py:
    Module(manifest: dict)                 — KHÔNG mở kết nối, không đọc mạng (được tạo thử khi nạp)
    nap(cau_hinh: dict) -> None            — nạp cấu hình ĐÃ KIỂM (khởi động/cài xong), không gọi mạng
    async cai(cau_hinh) -> dict            — kiểm cấu hình bằng cách gọi thật:
                                             {ok, ket_qua_tung_khoa: [...], loi, cau_hinh_luu (chỉ khi ok)}
                                             `cau_hinh_luu` là cấu hình sạch để lưu — lõi KHÔNG trả nó ra API
    async chat(body: dict, stream: bool)   -> (mã HTTP, headers, bytes | async-iterator[bytes])
    async models() -> list[dict]           — id dạng "<tien_to>/<tên>"
    async suc_khoe() -> dict               — không tốn token, không lộ khoá
    async dung() -> None                   — đóng kết nối

Mã module chạy TRONG tiến trình lõi (quyền như lõi). Được import `app.*` (vd app.chuyen_tiep)
và gói có sẵn trong ảnh; gói thiếu khai ở `yeu_cau_goi` để lõi từ chối sớm, rõ ràng.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("web2api.kham_pha")

ID_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
#: Tiền tố model: đứng trước «/» trong tên model nên không được chứa «/».
TIEN_TO_HOP_LE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")
#: Id trùng chữ của tuyến /api/modules/<chữ> thì `DELETE /api/modules/tu-xa` thành mơ hồ.
ID_DANH_RIENG = {"catalog", "thong-bao", "nap-lai", "tu-xa"}
KIEU_TRUONG = {"chuoi", "bi_mat", "danh_sach_bi_mat"}
TRUONG_CHUOI_BAT_BUOC = ("id", "ten", "mo_ta", "loai", "giao_thuc", "tien_to")
PHUONG_THUC = ("nap", "cai", "chat", "models", "suc_khoe", "dung")
_TEN_GOI = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LoiNap(Exception):
    pass


@dataclass
class ModuleKhamPha:
    id: str
    nguon: str  # "anh" | "du_lieu"
    thu_muc: Path
    manifest: dict
    lop: type
    van_tay: str
    ten_ma: str
    tien_to: str = field(init=False)

    def __post_init__(self):
        self.tien_to = self.manifest["tien_to"]

    @property
    def truong_cau_hinh(self) -> list[dict]:
        return self.manifest["truong_cau_hinh"]

    @property
    def can_cau_hinh(self) -> bool:
        return any(t.get("bat_buoc") for t in self.truong_cau_hinh)

    def tao(self):
        return self.lop(self.manifest)

    def duong_tab(self) -> dict | None:
        tab = self.manifest.get("tab_web")
        if not tab:
            return None
        return {"nhan": tab["nhan"], "duong": f"/m/{self.id}/{tab['trang'].removeprefix('web/')}"}


def kiem_manifest(m) -> str | None:
    """Lý do manifest không dùng được, hoặc None. Kiểu trường lạ là LỖI chứ không bỏ qua:
    kho dựa vào `kieu` để quyết bí mật — gõ nhầm «bi_mat » mà coi là chuỗi thường là lộ khoá."""
    if not isinstance(m, dict):
        return "manifest phải là đối tượng JSON"
    for k in TRUONG_CHUOI_BAT_BUOC:
        if not isinstance(m.get(k), str) or not m[k].strip():
            return f"thiếu hoặc sai trường «{k}» (cần chuỗi khác rỗng)"
    if not ID_HOP_LE.match(m["id"]) or m["id"] in ID_DANH_RIENG:
        return f"id {m['id']!r} không hợp lệ (cần ^[a-z0-9][a-z0-9_-]{{1,40}}$, không trùng tên tuyến)"
    if not TIEN_TO_HOP_LE.match(m["tien_to"]):
        return f"tien_to {m['tien_to']!r} không hợp lệ (chữ, số, . _ -; không có «/»)"
    truong = m.get("truong_cau_hinh")
    if not isinstance(truong, list):
        return "thiếu «truong_cau_hinh» (danh sách, có thể rỗng)"
    khoa_da_co = set()
    for i, t in enumerate(truong):
        if not isinstance(t, dict) or not isinstance(t.get("khoa"), str) or not isinstance(t.get("nhan"), str):
            return f"truong_cau_hinh[{i}] cần có «khoa» và «nhan» là chuỗi"
        if t.get("kieu") not in KIEU_TRUONG:
            return f"truong_cau_hinh[{i}] kiểu {t.get('kieu')!r} không hợp lệ (chỉ {sorted(KIEU_TRUONG)})"
        if t["khoa"] in khoa_da_co:
            return f"truong_cau_hinh trùng khoá {t['khoa']!r}"
        khoa_da_co.add(t["khoa"])
    if not isinstance(m.get("tai_nguyen"), dict):
        return "thiếu «tai_nguyen» (đối tượng)"
    mang = m["tai_nguyen"].get("mang_ra", [])
    if not isinstance(mang, list) or not all(isinstance(x, str) for x in mang):
        return "tai_nguyen.mang_ra phải là danh sách chuỗi"
    if "phien_ban" in m and not isinstance(m["phien_ban"], str):
        return "phien_ban phải là chuỗi"
    goi = m.get("yeu_cau_goi", [])
    if not isinstance(goi, list) or not all(isinstance(x, str) and _TEN_GOI.match(x) for x in goi):
        return "yeu_cau_goi phải là danh sách tên gói Python (chữ, số, _; không chấm)"
    tab = m.get("tab_web")
    if tab is not None:
        trang = tab.get("trang") if isinstance(tab, dict) else None
        if (not isinstance(tab, dict) or not isinstance(tab.get("nhan"), str) or not isinstance(trang, str)
                or not trang.startswith("web/") or ".." in trang.split("/") or "\\" in trang):
            return "tab_web cần {\"nhan\": chuỗi, \"trang\": \"web/<tệp>\"} (không «..»)"
    return None


def doc_manifest(thu_muc: Path) -> tuple[dict | None, str | None]:
    try:
        m = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "thiếu manifest.json"
    except (OSError, ValueError) as e:
        return None, f"manifest.json không đọc được: {e}"
    ly_do = kiem_manifest(m)
    return (None, ly_do) if ly_do else (m, None)


def goi_thieu(manifest: dict) -> list[str]:
    thieu = []
    for ten in manifest.get("yeu_cau_goi", []):
        try:
            if importlib.util.find_spec(ten) is None:
                thieu.append(ten)
        except (ImportError, ValueError):
            thieu.append(ten)
    return thieu


def van_tay(thu_muc: Path) -> str:
    """Băm NỘI DUNG mọi tệp (bỏ __pycache__): sửa một ký tự là đổi vân tay → nạp lại."""
    h = hashlib.sha256()
    for p in sorted(x for x in thu_muc.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(str(p.relative_to(thu_muc)).encode() + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def ten_ma(id_: str, vt: str) -> str:
    return f"web2api_module_{id_.replace('-', '_')}_{vt[:16]}"


def nap_ma(thu_muc: Path, manifest: dict, vt: str) -> tuple[type, str]:
    """Thực thi module.py dưới tên mô-đun DUY NHẤT theo (id + vân tay) → (lớp Module, tên mô-đun).

    Biên dịch thẳng từ MÃ NGUỒN, không qua __pycache__: .pyc được kiểm theo mtime (giây) + cỡ tệp,
    nên cài đè bản mới cùng cỡ trong cùng một giây sẽ chạy nhầm bytecode CŨ.
    Mọi lỗi của mã module (cú pháp, import, __init__) → LoiNap; lõi không sập.
    """
    thieu = goi_thieu(manifest)
    if thieu:
        raise LoiNap(f"thiếu gói Python: {', '.join(thieu)} — lõi không tự pip install lúc chạy; "
                     "hãy dùng module dạng từ xa (container riêng có đủ gói, đăng ký bằng /api/modules/tu-xa)")
    tep = thu_muc / "module.py"
    ten = ten_ma(manifest["id"], vt)
    spec = importlib.util.spec_from_file_location(ten, tep)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    try:
        ma = compile(tep.read_bytes(), str(tep), "exec")
        exec(ma, mod.__dict__)
        lop = getattr(mod, "Module", None)
        if not isinstance(lop, type):
            raise LoiNap("module.py không có lớp «Module»")
        thieu_pt = [p for p in PHUONG_THUC if not callable(getattr(lop, p, None))]
        if thieu_pt:
            raise LoiNap(f"lớp Module thiếu phương thức: {', '.join(thieu_pt)}")
        lop(manifest)  # tạo thử: lỗi trong __init__ lộ NGAY lúc nạp, không đợi lúc cài/khởi động
    except LoiNap:
        sys.modules.pop(ten, None)
        raise
    except FileNotFoundError:
        sys.modules.pop(ten, None)
        raise LoiNap("thiếu module.py") from None
    except BaseException as e:  # mã bên thứ ba: mọi lỗi (kể cả SystemExit) chỉ hỏng module này
        sys.modules.pop(ten, None)
        if isinstance(e, (KeyboardInterrupt,)):
            raise
        raise LoiNap(f"lỗi khi nạp module.py: {type(e).__name__}: {e}") from e
    return lop, ten


def quet(goc: Path) -> list[Path]:
    """Thư mục ứng viên trong một nguồn, xếp theo tên (để «module sau» khi trùng là xác định).
    Bỏ thư mục bắt đầu bằng «.» (thư mục tạm khi cài) hoặc «_» (__pycache__)."""
    if not goc.is_dir():
        return []
    return sorted(p for p in goc.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
