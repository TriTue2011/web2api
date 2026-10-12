"""Khám phá module từ CẤU TRÚC thư mục — không từ danh sách gõ tay (KE-HOACH-MODULE.md mục 8).

Thêm module = có một thư mục `<id>/` chứa `manifest.json` + `module.py` ở MỘT trong hai nguồn:
  - `app/modules/*/`             đi kèm ảnh, chỉ-đọc (hiện rỗng — lõi nhẹ);
  - `<WEB2API_DATA>/modules/*/`  cài lúc chạy (từ git), ghi được.
Không sửa dòng nào của lõi. Bảng khai riêng cho từng module là "thêm một danh sách nữa" —
hỏng đúng kiểu `_PREFETCH_MAP` của c2a (biết 3/109 công cụ).

MODULE NHIỀU TỆP: lõi nạp `module.py` như MỘT GÓI (tên gói duy nhất theo id + vân tay). Tệp anh em trong thư mục module
import bằng IMPORT TƯƠNG ĐỐI — `from .client import X` — KHÔNG `from modules.<id>.client import X` (đường đó chỉ có khi chạy
trong kho, lúc chạy thật module nằm ở <WEB2API_DATA>/modules/<id>/ nên vỡ «No module named 'modules'»).

HỢP ĐỒNG lớp `Module` trong module.py:
    Module(manifest: dict)                 — KHÔNG mở kết nối, không đọc mạng (được tạo thử khi nạp)
    nap(cau_hinh: dict) -> None            — nạp cấu hình ĐÃ KIỂM (khởi động/cài xong), không gọi mạng
    async cai(cau_hinh) -> dict            — kiểm cấu hình bằng cách gọi thật:
                                             {ok, ket_qua_tung_khoa: [...], loi, cau_hinh_luu (chỉ khi ok)}
                                             `cau_hinh_luu` là cấu hình sạch để lưu — lõi KHÔNG trả nó ra API
    async chat(body: dict, stream: bool)   -> (mã HTTP, headers, bytes | async-iterator[bytes])
    async models() -> list[dict]           — id dạng "<tien_to>/<tên>"
    async suc_khoe() -> dict               — không tốn token, không lộ khoá; không được đổi bộ đếm dùng
                                             {ok, base_url?, khoa: [mục mỗi khoá, THEO THỨ TỰ ĐÃ LƯU]}; mục mỗi khoá:
                                             khoa (dạng che …abcd), ok, do_tre_ms, so_model, loi, dang_nghi_den, và
                                             TUỲ CHỌN (thiếu thì giao diện ẩn phần đó, không lỗi):
                                               kha_nang  {mã: {trang_thai, ma?, thu_lai_luc?}}
                                               thu_tu    số thứ tự trong danh sách khoá (từ 1)
                                               dang_dung true ở khoá phục vụ yêu cầu GẦN NHẤT
                                               dung_cuoi / lan_429_cuoi  ISO hoặc null; so_429 số lần 429
                                               han_muc   [{ten: mã việc ("chat", "anh"…), da_dung: số lượt thành công,
                                                           toi_da: số hoặc null, hoi_luc: ISO hoặc null,
                                                           trang_thai: "dung_duoc" | "het_han_muc" | "bi_tu_choi" |
                                                                       "chua_ro", ma: mã HTTP hoặc null}]
                                             `toi_da` CHỈ điền khi nhà cung cấp cho biết trần thật, và khi ấy `da_dung` phải
                                             là mức đã dùng CÙNG KỲ với trần đó (cũng do nhà cung cấp báo) — giao diện vẽ thanh
                                             «còn toi_da − da_dung» khi có nó, không có thì chỉ hiện «đã dùng N lượt» + trạng
                                             thái; không bịa trần. Bộ chuyển tiếp dùng chung (app/chuyen_tiep.py) điền sẵn mọi
                                             trường này từ bộ đếm trong bộ nhớ (kể từ lần khởi động / lưu cấu hình gần nhất),
                                             `toi_da` luôn null.
    async dung() -> None                   — đóng kết nối

PHƯƠNG THỨC TUỲ CHỌN — có thì lõi mở đường /v1 tương ứng; không có thì lõi trả 501 (không vỡ):
    async anh(body: dict)                  -> (mã, headers, bytes)   POST /v1/images/generations (thân OpenAI)
    async video_tao(body: dict)            -> (mã, headers, bytes)   POST /v1/videos — JSON video OpenAI, "id" là
                                              mã CỦA MODULE (chỉ [A-Za-z0-9._-], không «:» «/»); lõi trả ra ngoài
                                              dạng "<tien_to>:<mã>" để biết module nào xử lý lượt hỏi sau
    async video_trang_thai(ma: str)        -> (mã, headers, bytes)   GET /v1/videos/{id} (lõi đã bỏ "<tien_to>:")
    async video_noi_dung(ma: str)          -> (mã, headers, bytes | async-iterator[bytes])
                                              GET /v1/videos/{id}/content — tệp lớn thì trả iterator (stream)
    Module có video phải có đủ cả ba phương thức video_*. Mã do khách gửi là ĐẦU VÀO KHÔNG TIN ĐƯỢC: module
    phải kiểm nó trước khi dùng để gọi dịch vụ kèm khoá.
    POST /v1/videos nhận cả multipart/form-data (SDK openai gửi videos.create như vậy): lõi đổi các trường chữ
    thành thân JSON cho video_tao (giá trị vẫn là CHUỖI, module tự ép kiểu); có phần TỆP (vd input_reference) thì
    lõi trả 415 — chưa có hợp đồng đưa tệp cho video_tao.
    async noi(body: dict)                  -> (mã, headers, bytes | async-iterator[bytes])
                                              POST /v1/audio/speech — thân JSON OpenAI (model, input, voice,
                                              response_format, speed, stream…); trả âm thanh nhị phân, module đặt
                                              content-type; iterator thì lõi stream từng khúc, không gom
    async nghe(yc: YeuCauDaPhan)           -> (mã, headers, bytes)   POST /v1/audio/transcriptions (multipart);
                                              lõi bảo đảm có đúng MỘT tệp "file"
    async anh_sua(yc: YeuCauDaPhan)        -> (mã, headers, bytes)   POST /v1/images/edits (multipart); lõi bảo
                                              đảm có ≥ 1 tệp "image" (khách gửi "image" hay "image[]" đều thành
                                              "image"), ≤ 1 tệp "mask", "prompt" là chuỗi khác rỗng
    YeuCauDaPhan (app/yeu_cau_da_phan.py) do lõi đọc từ multipart — module KHÔNG tự phân tích thân:
      .truong: dict[str, str | list[str]]  trường chữ, tên bỏ hậu tố «[]»; gửi một lần và không có «[]» → str,
                                           có «[]» hoặc lặp → list[str] theo thứ tự gửi (đúng hình thân JSON
                                           của cùng tham số). "model" vẫn giữ "<tien_to>/<tên>" như chat.
      .tep: list[TepTai]                   TepTai(ten_truong, ten_tep, content_type, noi_dung: bytes);
      .tep_ten(ten) -> list[TepTai]        các tệp của một trường.
    Trần TỔNG thân multipart: WEB2API_TEP_TOI_DA_MB (mặc định 25) — vượt là 413, module không được gọi. Tệp tạm
    lõi đã đóng trước khi gọi module. Mọi thứ trong YeuCauDaPhan (kể cả ten_tep, content_type) là KHÁCH KHAI,
    KHÔNG TIN ĐƯỢC: không dùng ten_tep làm đường dẫn, tự kiểm định dạng trước khi gửi đi kèm khoá.
    Header trả về do module chọn và lõi chuyển nguyên (riêng content-length của thân bytes thì lõi tự tính):
    đừng chép nguyên header của nhà cung cấp — content-encoding/content-length sai khi httpx đã giải nén.
    phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None
        — phân loại lỗi từ nhà cung cấp thượng nguồn về một trong các chuẩn loại lỗi trung lập (mã mở):
            "khoa_hong"        — khoá sai, hết hạn, bị thu hồi; lõi cho khoá nghỉ (1 giờ) và thử khoá kế.
            "het_han_muc"      — hết hạn mức / rate limit; lõi cho khoá nghỉ theo Retry-After và thử khoá kế.
            "khong_co_o_khoa"  — model hoặc khả năng không được cấp cho khoá này; chat: ghi sổ 6 giờ cho
                                 (khoá, model) và thử khoá kế; khả năng: nghỉ 24 giờ và thử khoá kế.
            "yeu_cau_sai"      — yêu cầu của khách không hợp lệ; lõi trả ngay, không đổi khoá, không phạt khoá.
            "may_chu_loi"      — lỗi phía máy chủ nhà cung cấp hoặc mạng (5xx); lõi thử thêm đúng một khoá.
            "chan_vung"        — máy chủ bị chặn vùng địa lý; lỗi của hạ tầng, lõi trả ngay, không phạt khoá.
            "khong_ro"         — chưa rõ lỗi do khoá hay do yêu cầu; lõi dùng so sánh giữa các khoá để phân xử.
        Mặc định khi module không cài hook hoặc trả về None:
            429           -> "het_han_muc"
            401, 403      -> "khoa_hong"
            500..599      -> "may_chu_loi"
            còn lại       -> "khong_ro"
        Mã lạ hoặc hook ném ngoại lệ -> coi là "khong_ro", không vỡ tiến trình.
        `loi` là từ điển lỗi chuẩn hoá {"error": {"message": ..., "type": ..., "code": ...}}; module được phép
        bổ sung gợi ý vào loi["error"]["message"] (lõi gọi hook ngay khi chuẩn hoá lỗi, trước khi trả cho khách).
        `boi_canh` chứa {"viec", "khoa", ...}. Lời giải thích khi khoá bị từ chối một khả năng tốn tiền thì module
        truyền cho bộ chuyển tiếp bằng tham số `goi_y_tu_choi_kha_nang` (một chuỗi), không qua hook.

Manifest khai khả năng ở trường MỞ `cung_cap` (tuỳ chọn): danh sách mã như ["chat", "anh", "video"]; quy ước
cho các phương thức trên: "noi", "nghe", "anh_sua" (cùng tên phương thức). Lõi chỉ
kiểm MẪU (MA_KHA_NANG), không giữ danh sách mã hợp lệ. Định tuyến /v1 dựa vào phương thức CÓ THẬT trên thể
hiện, không dựa vào lời khai; lời khai dùng cho giao diện (tab/chế độ nào hiện — GET /api/tinh-nang).

`loai` cũng là mã MỞ cùng mẫu MA_KHA_NANG (mục `can` trộn hai loại mã: "ai" là loại, "chat" là khả năng).
Tuỳ chọn cho giao diện: `loai_ten` (tên hiển thị của loại, vd "AI"), `loai_thu_tu` (số nguyên, nhỏ đứng
trước), `muc` — tab con/mục của module, mỗi mục {"id", "nhan", "can": [mã…]}: mục chỉ hiện khi module đã
liên kết VÀ mọi mã trong `can` có ở ít nhất một module đã liên kết (rỗng = luôn hiện khi đã liên kết).

Mã module chạy TRONG tiến trình lõi (quyền như lõi). Được import `app.*` (vd app.chuyen_tiep,
app.dia_chi, app.nhat_ky để ghi kênh riêng qua `nhat_ky.ghi(kenh, muc, tom_tat, ...)`) và gói có sẵn trong ảnh;
gói thiếu khai ở `yeu_cau_goi` để lõi từ chối sớm, rõ ràng.
Module cần địa chỉ gọi lại (OAuth/webhook) gọi `app.dia_chi.dia_chi_hien_tai(request)`: trả về URL gốc
công khai (ưu tiên địa chỉ đã đặt, biến WEB2API_PUBLIC_URL, hoặc suy ra từ Host). Địa chỉ suy ra do KHÁCH gửi:
gửi cho bên thứ ba thì dùng `lay_thong_tin_dia_chi(request)` và từ chối khi `dang_doan` là true.
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
#: Mẫu mã khả năng trong `cung_cap`, mã loại `loai` và mã trong `can` — kiểm dạng, KHÔNG liệt kê mã hợp lệ
#: (loại/khả năng mới không phải sửa lõi).
MA_KHA_NANG = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
#: Id của một mục (tab con) trong `muc` — đi vào id phần tử/đường dẫn của giao diện.
ID_MUC = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

#: Chuẩn phân loại lỗi trung lập cho module (mã mở):
LOAI_LOI_CHUAN = {
    "khoa_hong",
    "het_han_muc",
    "khong_co_o_khoa",
    "yeu_cau_sai",
    "may_chu_loi",
    "chan_vung",
    "khong_ro",
}


def phan_loai_loi_mac_dinh(ma: int) -> str:
    """Phân loại lỗi mặc định theo mã trạng thái HTTP chuẩn khi module không tự phân loại."""
    if ma == 429:
        return "het_han_muc"
    if ma in (401, 403):
        return "khoa_hong"
    if 500 <= ma < 600:
        return "may_chu_loi"
    return "khong_ro"


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

    def duong_tab(self) -> dict:
        """Tab của module trên thanh trên. Manifest khai `tab_web` thì dùng giao diện riêng ở /m/<id>/;
        không khai thì trỏ tới ô của module trên trang Hệ thống → Module — nơi form dựng từ `truong_cau_hinh`
        để thêm khoá. Module nào đã cài cũng có tab, không cần bảng tên module nào. Đường tương đối: chạy
        được cả khi web2api nằm sau proxy ở một thư mục con."""
        tab = self.manifest.get("tab_web")
        if not tab:
            return {"nhan": self.manifest["ten"], "duong": f"He-thong.dc.html#module-{self.id}"}
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
    if not MA_KHA_NANG.match(m["loai"]):
        return f"loai {m['loai']!r} không hợp lệ (mã: chữ thường, số, _; ví dụ \"ai\", \"app\", \"nha\")"
    if "loai_ten" in m and (not isinstance(m["loai_ten"], str) or not m["loai_ten"].strip()):
        return "loai_ten phải là chuỗi khác rỗng (tên hiển thị của loại)"
    if "loai_thu_tu" in m and (type(m["loai_thu_tu"]) is not int):
        return "loai_thu_tu phải là số nguyên (nhỏ đứng trước)"
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
    cung_cap = m.get("cung_cap", [])
    if (not isinstance(cung_cap, list) or not all(isinstance(x, str) and MA_KHA_NANG.match(x) for x in cung_cap)
            or len(set(cung_cap)) != len(cung_cap)):
        return 'cung_cap phải là danh sách mã khả năng không trùng (chữ thường, số, _; ví dụ ["chat", "anh"])'
    muc = m.get("muc", [])
    if not isinstance(muc, list):
        return 'muc phải là danh sách [{"id", "nhan", "can": [mã…]}]'
    id_muc = set()
    for i, x in enumerate(muc):
        if (not isinstance(x, dict) or not isinstance(x.get("id"), str) or not ID_MUC.match(x["id"])
                or not isinstance(x.get("nhan"), str) or not x["nhan"].strip() or not isinstance(x.get("can"), list)
                or not all(isinstance(c, str) and MA_KHA_NANG.match(c) for c in x["can"])):
            return (f'muc[{i}] cần {{"id": mã (chữ thường, số, - _), "nhan": chuỗi, "can": [mã khả năng/loại…]}} '
                    '(can rỗng = luôn hiện khi module đã liên kết)')
        if x["id"] in id_muc:
            return f"muc trùng id {x['id']!r}"
        id_muc.add(x["id"])
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


def go_ma(ten: str) -> None:
    """Gỡ mô-đun module khỏi bộ nhớ cùng MỌI tệp anh em đã import (ten.<tệp>): không giữ mã cũ."""
    for k in [k for k in sys.modules if k == ten or k.startswith(ten + ".")]:
        sys.modules.pop(k, None)


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
    # Nạp như một GÓI: các tệp anh em trong thư mục module import được bằng `from .tên import …` (tên gói duy nhất theo
    # id + vân tay nên hai module cùng có `client.py` không đụng nhau).
    spec = importlib.util.spec_from_file_location(ten, tep, submodule_search_locations=[str(thu_muc)])
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
        go_ma(ten)
        raise
    except FileNotFoundError:
        go_ma(ten)
        raise LoiNap("thiếu module.py") from None
    except BaseException as e:  # mã bên thứ ba: mọi lỗi (kể cả SystemExit) chỉ hỏng module này
        go_ma(ten)
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
