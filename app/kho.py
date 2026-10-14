"""Kho trên đĩa của web2api: cấu hình công khai, bí mật, ghi nguyên tử.

Hai lớp tách hẳn nhau vì hai mức rủi ro khác nhau:
- `<WEB2API_DATA>/modules.json`, `tu_xa.json`, `nguon_git.json` — CÔNG KHAI: được trả
  thẳng ra các GET mở (`/api/modules`), nên chỉ chứa giá trị thường và bí mật ĐÃ CHE.
- `<WEB2API_DATA>/bi_mat/<tên>.json` quyền 0600, thư mục 0700 — khoá API, token.
  Không API nào trả nội dung tệp này.

Trường nào là bí mật do manifest của module quyết (`kieu` = `bi_mat` | `danh_sach_bi_mat`),
không do danh sách tên gõ tay ở đây: module mới khai trường bí mật mới thì kho tự hiểu.

Ghi nguyên tử (tệp tạm cùng thư mục → fsync → os.replace): mất điện giữa chừng thì còn
bản cũ nguyên vẹn chứ không còn nửa tệp JSON làm hỏng cả danh sách module.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KIEU_BI_MAT = {"bi_mat", "danh_sach_bi_mat"}
#: Tên tệp bí mật: id module (^[a-z0-9][a-z0-9_-]{1,40}$) hoặc tên nội bộ có gạch dưới đầu
#: (`_nguon_git`) — gạch dưới đầu không hợp lệ với id module nên hai nhóm không bao giờ trùng.
_TEN_BI_MAT = re.compile(r"^_?[a-z0-9][a-z0-9_-]{1,40}\Z")

# Đọc-sửa-ghi modules.json/tu_xa.json phải tuần tự: hai yêu cầu cài cùng lúc không được
# đè mất mục của nhau. Khoá luồng (không phải asyncio) vì các hàm ở đây đồng bộ.
_KHOA = threading.RLock()


class KhoHong(Exception):
    """Tệp kho hỏng: KHÔNG tự ghi đè (sẽ xoá mất dữ liệu người dùng còn cứu được)."""


def thu_muc_du_lieu() -> Path:
    # Đọc mỗi lần gọi (không chốt lúc import) để test và người vận hành đổi được qua biến môi trường.
    return Path(os.environ.get("WEB2API_DATA", "/data"))


def thu_muc_modules() -> Path:
    return thu_muc_du_lieu() / "modules"


def bay_gio_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def che(gia_tri: str) -> str:
    """Dạng che «…abcd». Khoá ngắn (<12 ký tự) thì 4 ký tự cuối đã là phần lớn khoá → chỉ «…»."""
    gia_tri = str(gia_tri or "")
    return "…" + gia_tri[-4:] if len(gia_tri) >= 12 else "…"


def ghi_nguyen_tu(p: Path, noi_dung: str, quyen: int = 0o644) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tam = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tam")
    try:
        # Đặt quyền TRƯỚC khi ghi nội dung: không có khoảnh khắc nào bí mật nằm trong tệp đọc được.
        os.fchmod(fd, quyen)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(noi_dung)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tam, p)
    except BaseException:
        try:
            os.unlink(tam)
        except FileNotFoundError:
            pass
        raise


def doc_json(p: Path) -> Any | None:
    """None nếu chưa có tệp; tệp hỏng → KhoHong (nêu đường dẫn)."""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise KhoHong(f"{p} hỏng: {e}") from e


def _ghi_json(p: Path, du_lieu: Any, quyen: int = 0o644) -> None:
    ghi_nguyen_tu(p, json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen)


# ───────────── bí mật ─────────────

def ten_bi_mat_hop_le(ten) -> bool:
    """Một quy tắc tên tệp bí mật cho mọi nơi (kho, sao lưu/khôi phục) — fullmatch, không lệch `$`/`\\Z`."""
    return isinstance(ten, str) and _TEN_BI_MAT.fullmatch(ten) is not None


def giu_khoa_kho() -> threading.RLock:
    """Khoá đọc-sửa-ghi của kho — cho việc ghi NHIỀU tệp một lượt (khôi phục) không xen với PUT đang chạy."""
    return _KHOA


def _duong_bi_mat(ten: str) -> Path:
    if not ten_bi_mat_hop_le(ten):
        raise ValueError(f"tên bí mật không hợp lệ: {ten!r}")
    return thu_muc_du_lieu() / "bi_mat" / f"{ten}.json"


def doc_bi_mat(ten: str) -> dict:
    du_lieu = doc_json(_duong_bi_mat(ten))
    return du_lieu if isinstance(du_lieu, dict) else {}


def ghi_bi_mat(ten: str, du_lieu: dict) -> None:
    p = _duong_bi_mat(ten)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    _ghi_json(p, du_lieu, 0o600)


def xoa_bi_mat(ten: str) -> bool:
    try:
        _duong_bi_mat(ten).unlink()
        return True
    except FileNotFoundError:
        return False


# ───────────── modules.json (module có mã đã cài cấu hình) ─────────────

def _p_modules() -> Path:
    return thu_muc_du_lieu() / "modules.json"


def doc_modules() -> tuple[list[dict], str | None]:
    """(danh sách, cảnh báo). Chưa có tệp = container mới → rỗng, không cảnh báo.

    Tệp hỏng vẫn trả rỗng để trang không sập, nhưng KHÔNG im lặng: trả cảnh báo nêu
    đường dẫn để web hiện cho người dùng thấy (người gọi ghi log).
    """
    p = _p_modules()
    try:
        du_lieu = doc_json(p)
        if du_lieu is None:
            return [], None
        ds = du_lieu.get("modules") if isinstance(du_lieu, dict) else None
        if not isinstance(ds, list):
            raise KhoHong(f'{p} hỏng: cần dạng {{"modules": [...]}}')
    except KhoHong as e:
        return [], f"{e}; coi như chưa có cấu hình module nào"
    return ds, None


def kiem_tep_modules(du_lieu: Any) -> list[dict]:
    """Kiểm THUẦN (không ghi) nội dung modules.json: {"modules": [{"id", "cai_luc", "cau_hinh_cong_khai"}]}.
    Trả danh sách mục đã chuẩn hoá (chỉ ba trường lõi hiểu); sai → ValueError nêu vị trí, không chép giá trị."""
    from app.kham_pha import ID_HOP_LE

    ds = du_lieu.get("modules") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list):
        raise ValueError('cần dạng {"modules": [...]}')
    ra, da_thay = [], set()
    for i, m in enumerate(ds):
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not ID_HOP_LE.match(m["id"]):
            raise ValueError(f"modules[{i}]: «id» phải là id module hợp lệ")
        if m["id"] in da_thay:
            raise ValueError(f"modules[{i}]: «id» trùng mục trước")
        da_thay.add(m["id"])
        if not isinstance(m.get("cai_luc"), str):
            raise ValueError(f"modules[{i}]: «cai_luc» phải là chuỗi")
        if not isinstance(m.get("cau_hinh_cong_khai"), dict):
            raise ValueError(f"modules[{i}]: «cau_hinh_cong_khai» phải là đối tượng")
        ra.append({"id": m["id"], "cai_luc": m["cai_luc"], "cau_hinh_cong_khai": m["cau_hinh_cong_khai"]})
    return ra


def kiem_cau_hinh_cong_khai(truong: list[dict], cong_khai: dict) -> None:
    """Cấu hình công khai đã lưu phải khớp khai báo `truong_cau_hinh` của manifest module sở hữu: chỉ trường đã khai;
    trường thường là chuỗi; trường bí mật chỉ ở dạng ĐÃ CHE (chuỗi hoặc danh sách chuỗi). Sai → ValueError."""
    kieu = {t["khoa"]: t["kieu"] for t in truong}
    for k, v in cong_khai.items():
        if k not in kieu:
            raise ValueError("có trường không được manifest của module khai")
        if kieu[k] in KIEU_BI_MAT:
            if not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))):
                raise ValueError("trường bí mật (dạng che) phải là chuỗi hoặc danh sách chuỗi")
        elif not isinstance(v, str):
            raise ValueError("trường cấu hình thường phải là chuỗi")


def _doc_modules_de_sua() -> list[dict]:
    du_lieu = doc_json(_p_modules())
    if du_lieu is None:
        return []
    ds = du_lieu.get("modules") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list):
        raise KhoHong(f"{_p_modules()} hỏng — sửa hoặc xoá tay trước khi cài")
    return ds


def tach_cau_hinh(truong: list[dict], cau_hinh: dict) -> tuple[dict, dict]:
    """(công khai, bí mật) theo `kieu` từng trường của manifest; trường không khai thì bỏ.

    Phần công khai giữ bí mật ở dạng che để giao diện hiện «đã có 2 khoá …abcd, …wxyz».
    """
    cong_khai: dict = {}
    bi_mat: dict = {}
    for t in truong:
        k = t["khoa"]
        if k not in cau_hinh:
            continue
        v = cau_hinh[k]
        if t["kieu"] in KIEU_BI_MAT:
            bi_mat[k] = v
            cong_khai[k] = [che(x) for x in v] if isinstance(v, list) else che(v)
        else:
            cong_khai[k] = v
    return cong_khai, bi_mat


def luu_cai_dat(id_: str, truong: list[dict], cau_hinh: dict) -> dict:
    """Lưu cấu hình đã kiểm của một module. Bí mật ghi TRƯỚC: mục trong modules.json
    không bao giờ trỏ tới bí mật chưa có."""
    cong_khai, bi_mat = tach_cau_hinh(truong, cau_hinh)
    with _KHOA:
        ds = _doc_modules_de_sua()
        if bi_mat:
            ghi_bi_mat(id_, bi_mat)
        else:
            xoa_bi_mat(id_)
        muc = {"id": id_, "cai_luc": bay_gio_iso(), "cau_hinh_cong_khai": cong_khai}
        ds = [m for m in ds if not (isinstance(m, dict) and m.get("id") == id_)] + [muc]
        _ghi_json(_p_modules(), {"modules": ds})
    return muc


def ghep_cau_hinh(truong: list[dict], da_luu: dict, moi: dict) -> dict:
    """Cấu hình gửi lên + cấu hình đã lưu → cấu hình đầy đủ để module kiểm lại rồi lưu.

    - Trường VẮNG MẶT trong `moi`: giữ giá trị đã lưu — thêm khoá thì không phải dán lại khoá cũ,
      cũng không mất `base_url` đã đặt.
    - Trường có mặt: thay (danh sách thường vẫn là thay cả danh sách).
    - Trường kiểu `danh_sach_bi_mat` nhận thêm dạng sửa `{"giu": [chỉ số], "them": [khoá mới]}`:
      chỉ số trỏ vào danh sách đã lưu theo đúng thứ tự dạng che mà GET /api/modules/{id}/cau-hinh trả
      — thêm/bớt từng khoá mà không ai phải gửi lại (hay được thấy) khoá thật. Vắng «giu» = giữ hết.
      Chỉ số chứ không phải dạng che: hai khoá trùng 4 ký tự cuối, hay khoá ngắn (che thành «…»),
      thì dạng che không phân biệt được.
    Đầu vào sai → ValueError (lời nhắn không chứa giá trị khoá).
    """
    ra = dict(da_luu)
    kieu = {t["khoa"]: t["kieu"] for t in truong}
    for k, v in moi.items():
        if kieu.get(k) == "danh_sach_bi_mat" and isinstance(v, dict):
            thua = set(v) - {"giu", "them"}
            if thua:
                raise ValueError(f"«{k}» dạng sửa chỉ nhận «giu» và «them», thừa: {sorted(thua)}")
            cu = da_luu.get(k) if isinstance(da_luu.get(k), list) else []
            giu = v.get("giu", list(range(len(cu))))
            them = v.get("them", [])
            if not isinstance(giu, list) or not all(type(i) is int and 0 <= i < len(cu) for i in giu):
                raise ValueError(f"«{k}.giu» phải là danh sách chỉ số của khoá đã lưu (0..{len(cu) - 1})")
            if not isinstance(them, list) or not all(isinstance(x, str) for x in them):
                raise ValueError(f"«{k}.them» phải là danh sách chuỗi")
            v = list(dict.fromkeys([cu[i] for i in giu] + them))
        ra[k] = v
    return ra


def doc_cau_hinh(id_: str, truong: list[dict], muc: dict) -> dict:
    """Ghép lại cấu hình đầy đủ để `nap`: giá trị thường từ modules.json + bí mật từ bi_mat/."""
    bi_mat_k = {t["khoa"] for t in truong if t["kieu"] in KIEU_BI_MAT}
    cong_khai = muc.get("cau_hinh_cong_khai") or {}
    cau_hinh = {k: v for k, v in cong_khai.items() if k not in bi_mat_k}
    cau_hinh.update(doc_bi_mat(id_) if bi_mat_k else {})
    return cau_hinh


def xoa_cai_dat(id_: str) -> bool:
    with _KHOA:
        ds = _doc_modules_de_sua()
        con = [m for m in ds if not (isinstance(m, dict) and m.get("id") == id_)]
        co = len(con) != len(ds)
        if co:
            _ghi_json(_p_modules(), {"modules": con})
        co = xoa_bi_mat(id_) or co
    return co


# ───────────── tu_xa.json (module từ xa: chỉ URL, không mã) ─────────────

def _p_tu_xa() -> Path:
    return thu_muc_du_lieu() / "tu_xa.json"


def doc_tu_xa() -> list[dict]:
    du_lieu = doc_json(_p_tu_xa())
    if du_lieu is None:
        return []
    ds = du_lieu.get("modules") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list):
        raise KhoHong(f'{_p_tu_xa()} hỏng: cần dạng {{"modules": [...]}}')
    return ds


def luu_tu_xa(ban_ghi: dict, api_key: str | None) -> None:
    with _KHOA:
        ds = doc_tu_xa()
        if api_key:
            ghi_bi_mat(ban_ghi["id"], {"api_key": api_key})
        else:
            xoa_bi_mat(ban_ghi["id"])
        ds = [m for m in ds if m.get("id") != ban_ghi["id"]] + [ban_ghi]
        _ghi_json(_p_tu_xa(), {"modules": ds})


def xoa_tu_xa(id_: str) -> bool:
    with _KHOA:
        ds = doc_tu_xa()
        con = [m for m in ds if m.get("id") != id_]
        if len(con) == len(ds):
            return False
        _ghi_json(_p_tu_xa(), {"modules": con})
        xoa_bi_mat(id_)
        return True


def ghi_json_cong_khai(ten_tep: str, du_lieu: Any) -> None:
    """Ghi một tệp JSON công khai ở gốc thư mục dữ liệu (nguon_git.json, trạng thái…)."""
    _ghi_json(thu_muc_du_lieu() / ten_tep, du_lieu)


# ───────────── dia_chi.json (địa chỉ truy cập công khai đã đặt) ─────────────

def _p_dia_chi() -> Path:
    return thu_muc_du_lieu() / "dia_chi.json"


def doc_dia_chi_da_dat() -> str | None:
    """Đọc URL đã đặt từ <WEB2API_DATA>/dia_chi.json. None nếu chưa đặt; tệp hỏng → KhoHong (bên gọi báo cảnh báo)."""
    du_lieu = doc_json(_p_dia_chi())
    if du_lieu is None:
        return None
    url = du_lieu.get("url") if isinstance(du_lieu, dict) else None
    if not isinstance(url, str):
        raise KhoHong(f"{_p_dia_chi()} hỏng: thiếu trường «url» dạng chuỗi")
    return url.strip() or None


def luu_dia_chi_da_dat(url: str) -> None:
    """Lưu URL công khai đã đặt vào dia_chi.json (ghi nguyên tử)."""
    with _KHOA:
        _ghi_json(_p_dia_chi(), {"url": url})


def xoa_dia_chi_da_dat() -> bool:
    """Xoá tệp dia_chi.json."""
    with _KHOA:
        try:
            _p_dia_chi().unlink()
            return True
        except FileNotFoundError:
            return False


# ───────────── model.json (bật/tắt và mặc định model của từng module) ─────────────

def _p_model() -> Path:
    return thu_muc_du_lieu() / "model.json"


def doc_cai_dat_model() -> dict:
    """Đọc cấu hình model từ <WEB2API_DATA>/model.json.
    Dạng: {<id module>: {"tat": [id…], "mac_dinh": {<nhóm>: id}}}.
    Chưa có tệp = rỗng. Tệp hỏng → KhoHong.
    """
    du_lieu = doc_json(_p_model())
    if du_lieu is None:
        return {}
    if not isinstance(du_lieu, dict):
        raise KhoHong(f"{_p_model()} hỏng: cần dạng đối tượng JSON")
    return du_lieu


def kiem_cai_dat_model(du_lieu: Any) -> dict:
    """Kiểm THUẦN (không ghi) nội dung model.json: {<id module>: {"tat": [chuỗi], "mac_dinh": {chuỗi: chuỗi}}}.
    Trả bản chuẩn hoá; sai kiểu → ValueError (lời nhắn nêu vị trí, không chép giá trị)."""
    from app.kham_pha import ID_HOP_LE

    if not isinstance(du_lieu, dict):
        raise ValueError("cần dạng đối tượng {<id module>: {...}}")
    ra = {}
    for id_, muc in du_lieu.items():
        if not ID_HOP_LE.match(id_):
            raise ValueError("khoá phải là id module hợp lệ")
        if (not isinstance(muc, dict) or not isinstance(muc.get("tat", []), list)
                or not all(isinstance(x, str) for x in muc.get("tat", []))):
            raise ValueError("«tat» phải là danh sách chuỗi")
        mac_dinh = muc.get("mac_dinh", {})
        if not isinstance(mac_dinh, dict) or not all(isinstance(v, str) for v in mac_dinh.values()):
            raise ValueError("«mac_dinh» phải là đối tượng {nhóm: id model}")
        ra[id_] = {"tat": list(dict.fromkeys(muc.get("tat", []))), "mac_dinh": dict(mac_dinh)}
    return ra


def doc_cai_dat_model_module(id_: str) -> dict:
    """Lấy {"tat": [id…], "mac_dinh": {<nhóm>: id}} của một module."""
    tat_ca = doc_cai_dat_model()
    muc = tat_ca.get(id_)
    if not isinstance(muc, dict):
        return {"tat": [], "mac_dinh": {}}
    tat = muc.get("tat")
    if not isinstance(tat, list):
        tat = []
    mac_dinh = muc.get("mac_dinh")
    if not isinstance(mac_dinh, dict):
        mac_dinh = {}
    return {
        "tat": [x for x in tat if isinstance(x, str)],
        "mac_dinh": {k: v for k, v in mac_dinh.items() if isinstance(k, str) and isinstance(v, str)},
    }


def luu_cai_dat_model_module(id_: str, tat: list[str], mac_dinh: dict[str, str]) -> dict:
    """Lưu cấu hình model của module vào model.json (ghi nguyên tử, khoá luồng)."""
    with _KHOA:
        try:
            tat_ca = doc_cai_dat_model()
        except KhoHong:
            tat_ca = {}
        muc = {
            "tat": list(dict.fromkeys(tat)),
            "mac_dinh": dict(mac_dinh),
        }
        tat_ca[id_] = muc
        _ghi_json(_p_model(), tat_ca)
        return muc


def xoa_cai_dat_model_module(id_: str) -> bool:
    """Xoá cấu hình model của module khi gỡ."""
    with _KHOA:
        try:
            tat_ca = doc_cai_dat_model()
        except KhoHong:
            return False
        if id_ in tat_ca:
            del tat_ca[id_]
            _ghi_json(_p_model(), tat_ca)
            return True
        return False


# ───────────── combo.json (định tuyến dự phòng nhiều nguồn) ─────────────

def _p_combo() -> Path:
    return thu_muc_du_lieu() / "combo.json"


def doc_cai_dat_combo() -> dict:
    """Đọc THÔ <WEB2API_DATA>/combo.json: {<tên combo>: {"ten", "mo_ta", "kha_nang", "buoc": [{"model", "bat"}]}}.
    Chưa có tệp = rỗng. Tệp hỏng → KhoHong. Kiểm hình dạng từng mục là việc của app/combo.py (doc_tat_ca).
    """
    du_lieu = doc_json(_p_combo())
    if du_lieu is None:
        return {}
    if not isinstance(du_lieu, dict):
        raise KhoHong(f"{_p_combo()} hỏng: cần dạng đối tượng JSON")
    return du_lieu


def luu_mot_combo(ten: str, thong_tin: dict) -> dict:
    """Lưu hoặc cập nhật một combo vào combo.json quyền 0600 (nguyên tử, khoá luồng)."""
    with _KHOA:
        try:
            tat_ca = doc_cai_dat_combo()
        except KhoHong:
            tat_ca = {}
        muc = dict(thong_tin)
        muc["ten"] = ten
        tat_ca[ten] = muc
        _ghi_json(_p_combo(), tat_ca, quyen=0o600)
        return muc


def xoa_mot_combo(ten: str) -> bool:
    """Xoá một combo khỏi combo.json."""
    with _KHOA:
        try:
            tat_ca = doc_cai_dat_combo()
        except KhoHong:
            return False
        if ten in tat_ca:
            del tat_ca[ten]
            _ghi_json(_p_combo(), tat_ca, quyen=0o600)
            return True
        return False
