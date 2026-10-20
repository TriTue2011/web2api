"""Module hoa-don — đối chiếu hoá đơn điện tử (NĐ 123/2020, TT 78/2021) với bảng danh mục.

Port từ c2a `services/hoa_don.py` (commit d879b03, kho CỦA CHÍNH CHỦ MÁY — CHỈ ĐỌC khi
chép, không sửa c2a) theo LUẬT PORT: chép mã đang chạy thật, chỉ đổi lớp kết nối /
khuôn module / xác thực. Khác biệt so với c2a (ghi rõ từng điểm):

1. Không đọc tệp theo đường dẫn (`doc_xml/duong` của c2a): công cụ nhận NỘI DUNG qua
   tham số — XML dạng chuỗi (`xml`), danh mục là mảng dòng đã đọc (`danh_muc`) hoặc
   tệp .xlsx dạng base64 (`xlsx_base64`).
2. Không pandas (c2a `doc_bang` dùng pandas, mà không được thêm gói vào requirements
   lõi): đọc .xlsx bằng zipfile + ElementTree với trần giải nén; công thức KHÔNG bao
   giờ thực thi — chỉ dùng giá trị đã lưu, ô công thức không có giá trị lưu thì bỏ qua.
3. Số tiền/số lượng là **Decimal** (c2a `_so` trả float — so tiền bằng float là lớp lỗi
   «phép tính sai giả»): `_so` ở đây giữ nguyên ba khuôn nhận dạng số Việt/Anh của
   c2a (`office_bo_sung.py`), chỉ đổi float → Decimal ở cuối.
4. Không `pdf_intent.extract_markdown` (trích xuất PDF là phần của module `van-phong`,
   NV156, chưa gộp): điểm nối — nhận văn bản Markdown ĐÃ TRÍCH qua tham số
   `van_ban_pdf`, dùng lại `bang_tu_markdown`/`_neo_theo_ten` của c2a; báo cáo NÓI RÕ
   nguồn là PDF (bản in), kém chắc hơn XML.
5. Không `nhan_tep`/`dang_cho`/hồ sơ chờ tệp (c2a gắn kênh chat + `luu_tru_day`): công
   cụ là hàm thuần, không lưu trạng thái.
6. Không phụ thuộc module `van-phong` (NV156 chưa gộp): nếu sau này nó cung cấp phần
   đọc, nó cứ đưa dữ liệu đã đọc qua `danh_muc`/`van_ban_pdf` — điểm nối không đổi.

An toàn — hoá đơn/bảng là DỮ LIỆU KHÔNG TIN:
- XML: GIẢI MÃ đúng bảng mã thật (BOM/mẫu byte đầu; khai báo `encoding=` trong prolog
  phải khớp byte thật, lệch → từ chối) TRƯỚC, rồi mới kiểm DOCTYPE/khai báo ENTITY trên
  VĂN BẢN ĐÃ GIẢI MÃ — XML mã hoá UTF-16/32 không qua mặt được khâu chặn như khi dò
  chuỗi trên byte thô (soát độc lập B, E2). Từ chối mọi tài liệu có DOCTYPE/ENTITY
  (ElementTree dùng expat — thực thể nội bộ bị mở rộng, nguy cơ nổ thực thể
  «billion laughs»), trần kích thước (TRAN_XML), trần độ sâu lồng (TRAN_DO_SAU),
  trần số phần tử (TRAN_PHAN_TU). Mẫu hoá đơn lạ → báo «không đọc được», không đoán.
- Đối chiếu: ghép dòng bằng CHỈ MỤC BĂM theo mã/tên đã gập (tiền tính một lần mỗi dòng),
  không còn vòng lồng O(n·m); trần TRAN_DONG dòng mỗi bên; phần đối chiếu chạy NGOÀI
  vòng lặp sự kiện (asyncio.to_thread) để bảng lớn không chặn lõi (soát độc lập B, E3).
- .xlsx: đọc qua zipfile — trần kích thước nén, trần tổng giải nén, trần tỉ lệ nén
  (chống zip bomb), trần số tệp con, trần dòng/cột; chỉ đọc sheet1.
- Công thức không thực thi (xem điểm 2).
- Đầu ra bị cắt ở TRAN_DAU_RA; báo cáo liệt kê tối đa TRAN_DONG_LIET_KE tên cho mỗi
  nhóm «chỉ một bên», còn lại ghi số lượng. Không có gì của hoá đơn/bảng vào log.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import difflib
import io
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, deque
from decimal import Decimal, InvalidOperation
from typing import Any

#: Chênh tiền dưới ngần này đồng là làm tròn, không phải sai (Decimal cho chính xác).
LECH_TIEN = Decimal("1")
#: Tên hàng gần giống ngần này trở lên (0–1) thì coi là cùng mặt hàng khi không khớp đúng.
GIONG_TEN = 0.85
#: Các mức thuế GTGT Việt Nam — danh mục lệch hoá đơn ĐÚNG một trong các tỉ lệ (1 + mức) ở mọi dòng
#: thì danh mục ghi giá đã gồm thuế, không phải nhiều lỗi rời.
THUE_GTGT = (Decimal("0.05"), Decimal("0.08"), Decimal("0.10"))

#: Trần bảo vệ cho đầu vào không tin.
TRAN_XML = 2_000_000          # kích thước hoá đơn XML
TRAN_MD = 500_000             # văn bản Markdown đã trích từ PDF
TRAN_DO_SAU = 64              # độ sâu lồng XML
TRAN_PHAN_TU = 200_000        # số phần tử XML
TRAN_BANG_NEN = 10_000_000    # kích thước tệp .xlsx (nén)
TRAN_GIAI_NEN = 20_000_000    # tổng kích thước giải nén .xlsx
TRAN_TY_LE_NEN = 100          # tỉ lệ giải_nén/nén cho một tệp con — chống zip bomb
TRAN_TEP_CON = 5_000_000      # kích thước giải nén tối đa một tệp con
TRAN_SO_TEP = 2_000           # số mục trong zip
TRAN_DONG = 5_000             # số dòng bảng danh mục
TRAN_COT = 64                 # số cột một dòng
TRAN_DAU_RA = 80_000          # cắt báo cáo đầu ra (khớp van-phong)
TRAN_DONG_LIET_KE = 200       # liệt kê tối đa ngần này tên cho mỗi nhóm «chỉ một bên»
TRAN_UNG_VIEN = 16            # số ứng viên tối đa được chấm SequenceMatcher đầy đủ cho một dòng (đường gần giống)
TRAN_SHEET = 30               # tìm dòng tiêu đề trong 30 dòng đầu (khớp c2a)

#: Không gian tên OOXML (lưu sẵn để so thẻ theo hậu tố, không liệt kê danh sách phân loại XML).
_XMLNS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class LoiDuLieu(ValueError):
    """Dữ liệu ngoài sai khuôn hoặc vượt trần bảo vệ."""


# ── Số tiếng Việt / Anh → Decimal ─────────────────────────────────────────
#: Kiểu Việt — chấm phân nhóm nghìn, phẩy thập phân: 1.200 · 1.200,5 · 1.200.000
_RE_SO_VIET = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?")
#: Kiểu Anh — ngược lại: 1,200 · 1,200.5
_RE_SO_ANH = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
#: Không nhóm nghìn, nhiều nhất một dấu thập phân: 8 · 1200 · 1,5 · 1.5 · -2,5
_RE_SO_TRON = re.compile(r"[+-]?\d+(?:[.,]\d+)?")


def _so(x: Any) -> Decimal | None:
    """Chuỗi → Decimal, hiểu cả lối viết Việt lẫn Anh; None nếu không phải số.

    Khuôn nhận dạng giữ nguyên của c2a `services/office_bo_sung.py::_so` (commit
    d879b03) — phân biệt nhóm nghìn/thập phân bằng số chữ số sau dấu (đúng BA chữ
    số → nhóm nghìn). Chỉ đổi: trả Decimal thay vì float (tiền phải chuẩn xác).
    MST (chuỗi có số 0 đầu) không đưa qua đây — giữ nguyên chuỗi.
    """
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    if isinstance(x, int) and not isinstance(x, bool):
        return Decimal(x)
    if isinstance(x, float):
        return Decimal(str(x))
    s = str(x).strip().replace(" ", "").replace(" ", "")
    s = s.replace("%", "")
    if not s:
        return None
    if _RE_SO_VIET.fullmatch(s):
        s = s.replace(".", "").replace(",", ".")
    elif _RE_SO_ANH.fullmatch(s):
        s = s.replace(",", "")
    elif _RE_SO_TRON.fullmatch(s):
        s = s.replace(",", ".")
    else:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _so_chu(d: Decimal) -> str:
    """Decimal → chuỗi số nguyên/thập phân chuẩn để trả ra JSON (không qua float)."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _gap(s: Any) -> str:
    """Chữ thường, bỏ dấu, gộp khoảng trắng — để so tên cột và tên hàng (của c2a)."""
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9%]+", " ", s).strip()


# ── XML an toàn ─────────────────────────────────────────────────────────────
def _do_bang_ma(du_lieu: bytes) -> str:
    """Bảng mã THẬT của tài liệu XML, đoán theo BOM / mẫu byte đầu — đúng cách expat tự
    nhận diện (chuẩn XML, phụ lục F). Kiểm trên byte thô mà không giải mã là chỗ hở E2:
    DOCTYPE viết UTF-16 lẫn byte NUL nên mắt dò byte ASCII không thấy."""
    if du_lieu[:4] in (b"\x00\x00\xfe\xff", b"\x00\x00\x00<"):
        return "utf-32-be"
    if du_lieu[:4] in (b"\xff\xfe\x00\x00", b"<\x00\x00\x00"):
        return "utf-32-le"
    if du_lieu[:2] == b"\xfe\xff" or du_lieu[:4] == b"\x00<\x00?":
        return "utf-16-be"
    if du_lieu[:2] == b"\xff\xfe" or du_lieu[:4] == b"<\x00?\x00":
        return "utf-16-le"
    return "utf-8"


_RE_KHAI_BAO_MA = re.compile(r"""encoding\s*=\s*["']([^"']+)["']""")


def _ten_ma_chuan(s: str) -> str:
    """«UTF-16» / «utf_16» / «Utf16» → «utf16» để so khớp khai báo với bảng mã thật."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _giai_ma_xml(du_lieu: bytes) -> str:
    """Giải mã XML không tin về CHUỖI theo đúng bảng mã thật; khai báo `encoding=` trong
    prolog khác byte thật → từ chối (tài liệu mập mờ về bảng mã là dấu hiệu lẩn tránh)."""
    ma = _do_bang_ma(du_lieu)
    try:
        van_ban = du_lieu.decode(ma)
    except UnicodeDecodeError:
        raise LoiDuLieu(f"không giải mã được XML theo bảng mã {ma}") from None
    van_ban = van_ban.lstrip("\ufeff")  # bỏ dấu BOM đầu tài liệu (nếu có)
    kb = _RE_KHAI_BAO_MA.search(van_ban[:512])
    if kb:
        khai, that = _ten_ma_chuan(kb.group(1)), _ten_ma_chuan(ma)
        if khai != that and not (khai in ("utf16", "utf32") and that.startswith(khai)):
            raise LoiDuLieu(f"khai báo encoding «{kb.group(1)}» không khớp bảng mã thật "
                            f"({ma}) — từ chối")
    return van_ban


def _xml_an_toan(du_lieu: bytes, *, tran: int = TRAN_XML) -> ET.Element:
    """Phân tích XML từ dữ liệu không tin: chặn DOCTYPE/ENTITY, trần kích thước/độ sâu/số phần tử.

    ElementTree dùng expat — thực thể nội bộ trong DOCTYPE bị mở rộng (nguy cơ nổ
    thực thể «billion laughs»). Khâu chặn chạy trên VĂN BẢN ĐÃ GIẢI MÃ đúng bảng mã
    (xem `_giai_ma_xml`), không dò chuỗi trên byte thô — bản cũ bị qua mặt bằng XML
    mã hoá UTF-16 (soát độc lập B, E2). Hoá đơn TT78 thật không bao giờ có DOCTYPE.
    """
    if len(du_lieu) > tran:
        raise LoiDuLieu(f"dữ liệu XML {len(du_lieu)} byte vượt trần {tran} byte")
    van_ban = _giai_ma_xml(du_lieu)
    thap = van_ban.lower()
    if "<!doctype" in thap or "<!entity" in thap:
        raise LoiDuLieu("tài liệu XML có DOCTYPE/khai báo ENTITY — từ chối (chống nổ thực thể)")
    # Đã có chuỗi: bỏ khai báo <?xml ...?> (nó mang encoding= của byte gốc, không còn
    # ý nghĩa) để bộ phân tích không đọc lệch bảng mã khi mã hoá lại chuỗi.
    sach = re.sub(r"^<\?xml[\s\S]*?\?>", "", van_ban, count=1)
    try:
        goc = ET.fromstring(sach)
    except ET.ParseError as exc:
        raise LoiDuLieu(f"không đọc được XML: {str(exc)[:120]}") from None
    dem = 0
    ngan = [(goc, 1)]
    while ngan:
        e, sau = ngan.pop()
        dem += 1
        if dem > TRAN_PHAN_TU:
            raise LoiDuLieu(f"XML quá {TRAN_PHAN_TU} phần tử — từ chối")
        if sau > TRAN_DO_SAU:
            raise LoiDuLieu(f"XML lồng sâu quá {TRAN_DO_SAU} tầng — từ chối")
        ngan.extend((c, sau + 1) for c in e)
    return goc


def _ten(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _mot(goc: ET.Element | None, ten: str) -> ET.Element | None:
    if goc is None:
        return None
    return next((e for e in goc.iter() if _ten(e) == ten), None)


def _chu(goc: ET.Element | None, ten: str) -> str:
    e = _mot(goc, ten)
    return (e.text or "").strip() if e is not None and e.text else ""


def doc_xml(du_lieu: str | bytes) -> dict[str, Any]:
    """Hoá đơn XML (TT78) → cấu trúc. Tiền/số lượng là Decimal. Lỗi → {"loi": ...} (của c2a)."""
    if isinstance(du_lieu, str):
        data = du_lieu.encode("utf-8", errors="replace")
    elif isinstance(du_lieu, (bytes, bytearray)):
        data = bytes(du_lieu)
    else:
        return {"loi": f"hóa đơn phải là chuỗi XML, không phải {type(du_lieu).__name__}"}
    try:
        goc = _xml_an_toan(data)
    except LoiDuLieu as exc:
        return {"loi": str(exc)}
    hh = [e for e in goc.iter() if _ten(e) == "HHDVu"]
    if not hh:
        return {"loi": "XML không có danh sách hàng hóa (DSHHDVu/HHDVu) — có phải hóa đơn theo Thông tư 78?"}
    if len(hh) > TRAN_DONG:
        return {"loi": f"hoá đơn có {len(hh)} dòng hàng vượt trần {TRAN_DONG} dòng — từ chối"}
    dong = []
    for e in hh:
        dong.append({"stt": _chu(e, "STT"), "ten": _chu(e, "THHDVu"), "ma": _chu(e, "MHHDVu"),
                     "dvt": _chu(e, "DVTinh"), "sl": _so(_chu(e, "SLuong") or None),
                     "dg": _so(_chu(e, "DGia") or None), "ck": _so(_chu(e, "STCKhau") or None),
                     "tt": _so(_chu(e, "ThTien") or None), "thue_suat": _chu(e, "TSuat"),
                     "tinh_chat": _chu(e, "TChat")})
    tt = _mot(goc, "TToan")
    return {"nguon": "xml", "mau": _chu(goc, "KHMSHDon"), "ky_hieu": _chu(goc, "KHHDon"),
            "so": _chu(goc, "SHDon"), "ngay": _chu(goc, "NLap"),
            "ban": {"ten": _chu(_mot(goc, "NBan"), "Ten"), "mst": _chu(_mot(goc, "NBan"), "MST")},
            "mua": {"ten": _chu(_mot(goc, "NMua"), "Ten"), "mst": _chu(_mot(goc, "NMua"), "MST")},
            "dong": dong,
            "tong": {"chua_thue": _so(_chu(tt, "TgTCThue") or None), "thue": _so(_chu(tt, "TgTThue") or None),
                     "ck": _so(_chu(tt, "TTCKTMai") or None), "thanh_toan": _so(_chu(tt, "TgTTTBSo") or None)}}


# ── Nhận cột của bảng ──────────────────────────────────────────────────────
def _cot(tieu_de: Any) -> str | None:
    t = _gap(tieu_de)
    if not t:
        return None
    if t in ("stt", "tt") or t.startswith("so tt") or t.startswith("stt "):
        return "stt"
    if "thanh tien" in t or t.startswith("gia tri") or "thanh toan" in t:
        return "tt"
    if "don gia" in t or t in ("gia", "gia ban", "gia nhap"):
        return "dg"
    if "so luong" in t or t in ("sl", "kl", "khoi luong"):
        return "sl"
    if "don vi" in t or t == "dvt":
        return "dvt"
    if "thue suat" in t or t in ("% thue", "vat", "thue %"):
        return "thue_suat"
    if t.startswith("ma ") or t in ("ma", "ma hang", "ma vt", "ma sp"):
        return "ma"
    if ("ten" in t.split() or "hang hoa" in t or "mat hang" in t or "dien giai" in t or "san pham" in t
            or "vat tu" in t or "dich vu" in t):
        return "ten"
    return None


def _anh_xa(hang: list[Any]) -> dict[str, int]:
    """Một dòng tiêu đề → {khoá: chỉ số cột}. Cột đầu tiên khớp thắng."""
    ra: dict[str, int] = {}
    for i, o in enumerate(hang):
        k = _cot(o)
        if k and k not in ra:
            ra[k] = i
    return ra


def _du_cot(m: dict[str, int]) -> bool:
    return "ten" in m and len({"sl", "dg", "tt"} & set(m)) >= 2


def _dong_tu_hang(m: dict[str, int], hang: list[Any]) -> dict[str, Any] | None:
    def o(k: str) -> Any:
        i = m.get(k)
        return hang[i] if i is not None and i < len(hang) else None
    ten = str(o("ten") or "").strip()
    # Tên hàng phải có chữ: dòng đánh số cột của mẫu in («1 | 2 | 3 | 4 | 5 | 6 = 4 x 5») không phải hàng.
    if not re.search(r"[^\W\d_]", ten) or ten.lower() == "nan" or _gap(ten).startswith(("cong", "tong")):
        return None
    sl, dg, tt = _so(o("sl")), _so(o("dg")), _so(o("tt"))
    if sl is None and dg is None and tt is None:
        return None
    return {"stt": str(o("stt") or "").strip(), "ten": ten, "ma": str(o("ma") or "").strip(),
            "dvt": str(o("dvt") or "").strip(), "sl": sl, "dg": dg, "tt": tt,
            "thue_suat": str(o("thue_suat") or "").strip()}


def bang_tu_markdown(md: str) -> list[dict[str, Any]]:
    """Dòng hàng từ bảng Markdown đầu tiên có đủ cột tên hàng + hai trong ba cột số lượng / đơn giá / thành
    tiền. Mẫu in hoá đơn hay có dòng đánh số cột (1, 2, 3, 4=2x3…) ngay dưới tiêu đề — bỏ qua tự nhiên vì
    không có tên hàng."""
    m: dict[str, int] | None = None
    ra: list[dict[str, Any]] = []
    for dong in md.splitlines():
        s = dong.strip()
        if not (s.startswith("|") and s.endswith("|")):
            if m is not None and ra:
                break
            continue
        o = [x.strip() for x in s.strip("|").split("|")]
        if re.fullmatch(r"[\s:|+-]*", s.replace("|", "")):
            continue
        if m is None:
            mm = _anh_xa(o)
            if _du_cot(mm):
                m = mm
            continue
        d = _dong_tu_hang(m, o)
        if d:
            ra.append(d)
    return ra


def _tu(md: str) -> list[tuple[str, str]]:
    """Chữ hoá đơn → dãy (từ gốc, từ đã gập). Bỏ dấu bảng / định dạng Markdown."""
    sach = re.sub(r"\|", " ", re.sub(r"</?u>|\*+", "", md))
    ra = []
    for goc in sach.split():
        for con in _gap(goc).split():
            ra.append((goc, con))
    return ra


def _sau_nhan(tu: list[tuple[str, str]], nhan: list[str]) -> Decimal | None:
    """Số ĐẦU TIÊN sau nhãn (vd «cộng tiền hàng») trong dãy từ."""
    con = [c for _, c in tu]
    for i in range(len(con) - len(nhan) + 1):
        if con[i:i + len(nhan)] == nhan:
            for goc, _ in tu[i + len(nhan):i + len(nhan) + 12]:
                v = _so(goc.strip(":();"))
                if v is not None:
                    return v
    return None


def _neo_theo_ten(tu: list[tuple[str, str]], ten: list[str]) -> list[dict[str, Any]]:
    """Tìm từng TÊN HÀNG (của danh mục) trong chữ hoá đơn rồi đọc đơn vị + ba số đi sau (số lượng, đơn giá,
    thành tiền). Không cần bảng trích đúng — bản in PDF hay bị dính cột, sang trang thì thành một dòng chữ liền."""
    con = [c for _, c in tu]
    da_dung: set[int] = set()
    ra = []
    for t in ten:
        mau = _gap(t).split()
        if not mau:
            continue
        i = next((i for i in range(len(con) - len(mau) + 1)
                  if i not in da_dung and con[i:i + len(mau)] == mau), None)
        if i is None:
            continue
        da_dung.add(i)
        j, goc_cuoi = i + len(mau), tu[i + len(mau) - 1][0]
        while j < len(tu) and tu[j][0] == goc_cuoi:          # từ gốc cuối của tên còn từ con chưa khớp
            j += 1
        dvt, so = "", []
        seen = None
        for goc, _ in tu[j:j + 12]:
            if goc is seen:
                continue
            seen = goc
            v = _so(goc)
            if v is None:
                if so:
                    break
                dvt = dvt or goc
                continue
            so.append(v)
            if len(so) == 3:
                break
        if len(so) == 3:
            ra.append({"stt": "", "ten": t, "ma": "", "dvt": dvt, "sl": so[0], "dg": so[1], "tt": so[2],
                       "thue_suat": ""})
    return ra


def doc_tu_markdown(md: str, ten_goi_y: list[str] | None = None) -> dict[str, Any]:
    """Điểm nối cho PDF: văn bản đã trích (module van-phong trích bằng OfficeCLI) → cấu trúc hoá đơn.

    C2a `doc_pdf` gọi `pdf_intent.extract_markdown` ở bước này; điểm nối tách bước trích khỏi
    bước phân tích để module van-phong (NV156) làm phần trích sau. Báo cáo nói rõ nguồn là
    PDF (bản in) — kém chắc hơn file XML gốc.
    """
    tu = _tu(md)
    dong = bang_tu_markdown(md)
    cach = "bang"
    if not dong and ten_goi_y:
        dong, cach = _neo_theo_ten(tu, ten_goi_y), "neo"
    if not dong:
        return {"loi": "không tìm thấy bảng hàng hóa trong văn bản (cột tên hàng, số lượng, đơn giá, thành "
                       "tiền) — gửi file XML gốc của hóa đơn sẽ chắc hơn"}
    ky = re.search(r"(?:Ký hiệu|Serial)\W+(?:\(Serial\)\W*)?:?\s*([0-9A-Z]{6,8})\b", re.sub(r"[*_]", "", md))
    return {"nguon": "pdf", "cach_doc": cach, "so": "", "ky_hieu": ky.group(1) if ky else "", "mau": "",
            "ngay": "", "ban": {}, "mua": {}, "dong": dong,
            "tong": {"chua_thue": _sau_nhan(tu, ["cong", "tien", "hang"]),
                     "thue": _sau_nhan(tu, ["tien", "thue", "gtgt"]),
                     "thanh_toan": _sau_nhan(tu, ["tong", "cong", "tien", "thanh", "toan"])}}


# ── Đọc .xlsx bằng zipfile (không pandas) ──────────────────────────────────
def _doc_zip_an_toan(data: bytes) -> zipfile.ZipFile:
    """Mở zip sau khi đã qua trần — chống zip bomb theo CẤU TRÚC, không theo tên."""
    if len(data) > TRAN_BANG_NEN:
        raise LoiDuLieu(f"tệp bảng {len(data)} byte vượt trần {TRAN_BANG_NEN} byte")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise LoiDuLieu(f"không đọc được bảng: không phải tệp .xlsx (zip): {str(exc)[:60]}") from None
    muc = z.infolist()
    if len(muc) > TRAN_SO_TEP:
        raise LoiDuLieu(f"tệp .xlsx có {len(muc)} mục vượt trần {TRAN_SO_TEP}")
    tong = 0
    for i in muc:
        if i.file_size > TRAN_TEP_CON:
            raise LoiDuLieu(f"tệp con {i.filename} giải nén {i.file_size} byte vượt trần {TRAN_TEP_CON}")
        tong += i.file_size
        if i.file_size > 100_000 and i.compress_size > 0 \
                and i.file_size / i.compress_size > TRAN_TY_LE_NEN:
            raise LoiDuLieu(f"tệp con {i.filename} có tỉ lệ nén vượt trần {TRAN_TY_LE_NEN}× "
                            "(dấu hiệu zip bomb)")
    if tong > TRAN_GIAI_NEN:
        raise LoiDuLieu(f"tổng giải nén {tong} byte vượt trần {TRAN_GIAI_NEN}")
    return z


def _doc_muc(z: zipfile.ZipFile, ten: str) -> bytes:
    """Đọc một mục zip với trần (đọc thật, không tin kích thước khai báo trong header)."""
    try:
        with z.open(ten) as f:
            data = f.read(TRAN_TEP_CON + 1)
    except KeyError as exc:
        raise LoiDuLieu(f"tệp .xlsx thiếu {ten}") from exc
    if len(data) > TRAN_TEP_CON:
        raise LoiDuLieu(f"tệp con {ten} dài quá trần {TRAN_TEP_CON} byte")
    return data


def _chu_o(x: ET.Element) -> str:
    """Nối mọi văn bản trong một ô <is>/<si> (OOXML cho phép một ô nhiều <t>)."""
    return "".join(t.text or "" for t in x.iter() if _ten(t) == "t")


def _o_gia_tri(c: ET.Element, ss: list[str]) -> str | None:
    """Giá trị ô — CÔNG THỨC KHÔNG BAO GIỜ THỰC THI: chỉ dùng giá trị đã lưu (<v>),
    ô công thức không có <v> thì bỏ qua (None)."""
    t = c.get("t")
    if t == "inlineStr":
        is_ = next((e for e in c if _ten(e) == "is"), None)
        return _chu_o(is_) if is_ is not None else None
    v = next((e for e in c if _ten(e) == "v"), None)
    if v is None or v.text is None:
        return None
    tho = v.text.strip()
    if t == "s":
        try:
            return ss[int(tho)]
        except (ValueError, IndexError):
            return None
    return tho       # "str" (kết quả công thức dạng chữ), "n", "b": giá trị thô, không chạy công thức


def _ten_cot(tham_chieu: str) -> int | None:
    chu = "".join(c for c in str(tham_chieu) if c.isalpha()).upper()
    if not chu:
        return None
    n = 0
    for c in chu:
        n = n * 26 + (ord(c) - 64)
    return n - 1


def doc_xlsx_hang(data: bytes) -> list[list[Any]] | dict[str, Any]:
    """Tệp .xlsx → mảng dòng (list[list]) các giá trị ô dạng chuỗi; lỗi → {"loi": ...}."""
    try:
        z = _doc_zip_an_toan(data)
    except LoiDuLieu as exc:
        return {"loi": str(exc)}
    ten = sorted(n for n in z.namelist()
                 if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    if not ten:
        return {"loi": "tệp .xlsx không có sheet nào"}
    try:
        sheet = _xml_an_toan(_doc_muc(z, ten[0]), tran=TRAN_TEP_CON)
    except LoiDuLieu as exc:
        return {"loi": str(exc)}
    ss: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        try:
            goc = _xml_an_toan(_doc_muc(z, "xl/sharedStrings.xml"), tran=TRAN_TEP_CON)
        except LoiDuLieu as exc:
            return {"loi": str(exc)}
        for si in goc:
            if _ten(si) == "si":
                ss.append(_chu_o(si))
    hang: list[list[Any]] = []
    for r in sheet.iter():
        if _ten(r) != "row":
            continue
        dong: list[Any] = []
        for c in r:
            if _ten(c) != "c":
                continue
            vi_tri = _ten_cot(c.get("r", ""))
            if vi_tri is None or vi_tri > TRAN_COT:
                continue
            while len(dong) <= vi_tri:
                dong.append(None)
            dong[vi_tri] = _o_gia_tri(c, ss)
        hang.append(dong)
        if len(hang) > TRAN_DONG:
            return {"loi": f"bảng quá {TRAN_DONG} dòng — từ chối"}
    return hang


def doc_bang_tu_hang(hang: list[list[Any]]) -> dict[str, Any]:
    """Mảng dòng (đã đọc sẵn — điểm nối cho van-phong) → cấu trúc danh mục; lỗi → {"loi": ...}."""
    if not isinstance(hang, list) or not hang:
        return {"loi": "danh mục phải là mảng các dòng (không rỗng)"}
    if len(hang) > TRAN_DONG:
        return {"loi": f"danh mục quá {TRAN_DONG} dòng"}
    for i, h in enumerate(hang[:TRAN_SHEET]):
        if not isinstance(h, list):
            continue
        m = _anh_xa(h)
        if _du_cot(m):
            dong = [d for d in (_dong_tu_hang(m, x) for x in hang[i + 1:]) if d]
            if dong:
                return {"nguon": "bang", "dong": dong}
    return {"loi": "không tìm thấy dòng tiêu đề có cột tên hàng và hai trong ba cột số lượng / đơn giá / "
                   "thành tiền"}


def doc_xlsx_dong(data: bytes) -> dict[str, Any]:
    """Tệp .xlsx (bytes) → {"dong": [...]} (số đã tuần tự hoá thành chuỗi) hoặc {"loi": ...}."""
    hang = doc_xlsx_hang(data)
    if isinstance(hang, dict):
        return hang
    return _ra_json(doc_bang_tu_hang(hang))


# ── Đối chiếu ───────────────────────────────────────────────────────────────
def _bang_nhau(a: Decimal | None, b: Decimal | None, tien: bool) -> bool:
    if a is None or b is None:
        return True                     # một bên không ghi thì không có gì để so
    chenh = abs(a - b)
    if tien:
        return chenh <= LECH_TIEN
    return chenh <= max(Decimal(1), abs(a)) * Decimal("1e-6")


def _ghep(hd: list[dict[str, Any]], dm: list[dict[str, Any]]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Cặp (dòng hoá đơn, dòng danh mục): theo MÃ nếu cả hai có, rồi tên khớp đúng, rồi tên gần giống nhất
    (≥ GIONG_TEN, mỗi dòng danh mục ghép một lần).

    Bản cũ duyệt vòng lồng O(n·m) và gọi `_gap` (NFD + regex) cho MỖI cặp so sánh — hoá
    đơn 200 dòng × danh mục 200 dòng mất ~7 giây đồng bộ trong vòng lặp sự kiện (soát
    độc lập B, E3). Bản này: tiền tính khoá đã gập MỘT LẦN mỗi dòng, ghép mã/tên khớp
    đúng bằng CHỈ MỤC BĂM; đường gần giống chấm cận trên rẻ cho mọi ứng viên còn trống
    rồi chỉ chấm SequenceMatcher đầy đủ cho tối đa TRAN_UNG_VIEN ứng viên cao nhất;
    trần TRAN_DONG dòng mỗi bên.
    """
    if len(hd) > TRAN_DONG or len(dm) > TRAN_DONG:
        raise LoiDuLieu(f"đối chiếu tối đa {TRAN_DONG} dòng mỗi bên "
                        f"(hoá đơn {len(hd)} dòng, danh mục {len(dm)} dòng) — từ chối")
    ma_hd = [_gap(d.get("ma")) if d.get("ma") else "" for d in hd]
    ten_hd = [_gap(d.get("ten")) for d in hd]
    ma_dm = [_gap(d.get("ma")) if d.get("ma") else "" for d in dm]
    ten_dm = [_gap(d.get("ten")) for d in dm]
    chi_muc_ma: dict[str, deque[int]] = {}
    for j, k in enumerate(ma_dm):
        if k:                       # mã rỗng không phải khoá — không ghép theo mã trống
            chi_muc_ma.setdefault(k, deque()).append(j)
    chi_muc_ten: dict[str, deque[int]] = {}
    for j, k in enumerate(ten_dm):
        chi_muc_ten.setdefault(k, deque()).append(j)

    def _lay(chi_muc: dict[str, deque[int]], khoa: str, con: set[int]) -> int | None:
        """Chỉ số danh mục nhỏ nhất còn trống mang khoá này (đúng thứ tự duyệt của bản cũ)."""
        hang_doi = chi_muc.get(khoa)
        while hang_doi:
            j = hang_doi.popleft()
            if j in con:
                return j
        return None

    # Đường gần giống: chấm CẬN TRÊN rẻ (quick ratio theo đếm ký tự, tính sẵn một lần)
    # cho mọi ứng viên còn trống, chỉ chấm ĐẦY ĐỦ (SequenceMatcher) tối đa TRAN_UNG_VIEN
    # ứng viên cao nhất — trần số ứng viên theo gợi ý của soát độc lập B (E3). Cặp chọn
    # ra vẫn phải ≥ GIONG_TEN; ngang điểm thì lấy chỉ số lớn hơn (đúng bản cũ).
    dem_dm = [Counter(t) for t in ten_dm]
    dai_dm = [len(t) for t in ten_dm]

    def _can_tren(ca: dict, la: int, cb: dict, lb: int) -> float:
        if la + lb == 0:
            return 1.0
        if len(ca) > len(cb):
            ca, cb, la, lb = cb, ca, lb, la
        dung = 0
        for ch, n in ca.items():
            m = cb.get(ch)
            if m:
                dung += n if n < m else m
        return 2.0 * dung / (la + lb)

    con: set[int] = set(range(len(dm)))
    cap: list[tuple[int, int]] = []
    chua: list[int] = []
    sm = difflib.SequenceMatcher()
    for i in range(len(hd)):
        j = _lay(chi_muc_ma, ma_hd[i], con) if ma_hd[i] else None
        if j is None:
            j = _lay(chi_muc_ten, ten_hd[i], con)
        if j is None and con:
            ca, la = Counter(ten_hd[i]), len(ten_hd[i])
            xep = sorted(((_can_tren(ca, la, dem_dm[k], dai_dm[k]), k) for k in con), reverse=True)
            sm.set_seq1(ten_hd[i])
            tot, j_tot = 0.0, None
            for qr, k in xep[:TRAN_UNG_VIEN]:
                if qr < GIONG_TEN:
                    break
                sm.set_seq2(ten_dm[k])
                r = sm.ratio()
                if r >= GIONG_TEN and (j_tot is None or (r, k) > (tot, j_tot)):
                    tot, j_tot = r, k
            j = j_tot
        if j is None:
            chua.append(i)
        else:
            con.discard(j)
            cap.append((i, j))
    return cap, chua, sorted(con)


def _tien(x: Decimal | None) -> str:
    if x is None:
        return "—"
    s = f"{x:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def kiem_phep_tinh(hd: dict[str, Any]) -> list[str]:
    """Tự kiểm hoá đơn: số lượng × đơn giá − chiết khấu = thành tiền; tổng dòng = tổng chưa thuế;
    chưa thuế + thuế = thanh toán."""
    loi = []
    for d in hd["dong"]:
        if d.get("sl") is not None and d.get("dg") is not None and d.get("tt") is not None:
            tinh = d["sl"] * d["dg"] - (d.get("ck") or Decimal(0))
            if not _bang_nhau(tinh, d["tt"], True):
                loi.append(f"«{d['ten']}»: {_tien(d['sl'])} × {_tien(d['dg'])}"
                           + (f" − {_tien(d['ck'])}" if d.get("ck") else "")
                           + f" = {_tien(tinh)}, hóa đơn ghi {_tien(d['tt'])}")
    t = hd.get("tong") or {}
    hang = [d["tt"] for d in hd["dong"] if d.get("tt") is not None and d.get("tinh_chat") not in ("3", "4")]
    if t.get("chua_thue") is not None and hang:
        tong_hang = sum(hang, Decimal(0))
        # Dòng chiết khấu thương mại (TChat 3) và ghi chú (4) không cộng như hàng — so với tổng đã trừ chiết khấu.
        if not _bang_nhau(tong_hang - (t.get("ck") or Decimal(0)), t["chua_thue"], True) and \
                not _bang_nhau(tong_hang, t["chua_thue"], True):
            loi.append(f"tổng các dòng {_tien(tong_hang)} ≠ tổng chưa thuế {_tien(t['chua_thue'])}")
    if None not in (t.get("chua_thue"), t.get("thue"), t.get("thanh_toan")):
        if not _bang_nhau(t["chua_thue"] + t["thue"], t["thanh_toan"], True):
            loi.append(f"chưa thuế {_tien(t['chua_thue'])} + thuế {_tien(t['thue'])} ≠ thanh toán "
                       f"{_tien(t['thanh_toan'])}")
    return loi


def _cat_dau_ra(s: str) -> str:
    return s if len(s) <= TRAN_DAU_RA else s[:TRAN_DAU_RA] + "\n…[cat]"


def _liet_ke(ten: list[str]) -> str:
    if len(ten) <= TRAN_DONG_LIET_KE:
        return "; ".join(ten)
    return "; ".join(ten[:TRAN_DONG_LIET_KE]) + f"; … và còn {len(ten) - TRAN_DONG_LIET_KE} mục nữa"


def doi_chieu(hd: dict[str, Any], dm: dict[str, Any], *, ten_hd: str = "", ten_dm: str = "") -> str:
    """Báo cáo Markdown: phép tính trên hoá đơn, dòng khớp, dòng lệch (trường nào, hai bên ghi gì),
    dòng chỉ có một bên. Báo cáo bị cắt ở TRAN_DAU_RA; đầu ra là DỮ LIỆU KHÔNG TIN."""
    cap, chi_hd, chi_dm = _ghep(hd["dong"], dm["dong"])
    lech: list[tuple[str, str, str, str]] = []
    for i, j in cap:
        a, b = hd["dong"][i], dm["dong"][j]
        for k, nhan, tien in (("sl", "số lượng", False), ("dg", "đơn giá", True), ("tt", "thành tiền", True)):
            if not _bang_nhau(a.get(k), b.get(k), tien):
                lech.append((a["ten"], nhan, _tien(a.get(k)), _tien(b.get(k))))
        if a.get("dvt") and b.get("dvt") and _gap(a["dvt"]) != _gap(b["dvt"]):
            lech.append((a["ten"], "đơn vị", a["dvt"], b["dvt"]))
        if _gap(a["ten"]) != _gap(b["ten"]):
            lech.append((a["ten"], "tên (ghép gần đúng)", a["ten"], b["ten"]))
    ti_le = [dm["dong"][j]["dg"] / hd["dong"][i]["dg"] for i, j in cap
             if hd["dong"][i].get("dg") and dm["dong"][j].get("dg")
             and not _bang_nhau(hd["dong"][i]["dg"], dm["dong"][j]["dg"], True)]
    gom_thue = None
    if len(ti_le) >= 3 and len(ti_le) == len(cap):
        r = sorted(ti_le)[len(ti_le) // 2]
        gom_thue = next((t for t in THUE_GTGT
                         if abs(r - 1 - t) < Decimal("0.002") and all(abs(x - r) < Decimal("0.002") for x in ti_le)),
                        None)
    so = "-".join(x for x in (hd.get("ky_hieu"), hd.get("so")) if x) or ten_hd
    ra = [f"# Đối chiếu hóa đơn {so} với {ten_dm or 'danh mục'}"]
    if hd.get("nguon") == "pdf":
        ra.append("\n⚠️ Đọc từ **PDF** (bản in) — kém chắc hơn file XML gốc; nghi ngờ thì gửi XML."
                  + (" PDF trích bảng lệch cột nên em tìm từng tên hàng của danh mục trong hoá đơn; mặt hàng "
                     "hoá đơn có mà danh mục không có thì chỉ lộ ra qua tổng tiền." if hd.get("cach_doc") == "neo"
                     else ""))
    t = hd.get("tong") or {}
    if hd.get("ban", {}).get("ten") or hd.get("ngay"):
        ra.append(f"\nNgười bán: {hd['ban'].get('ten') or '—'} (MST {hd['ban'].get('mst') or '—'}); ngày lập "
                  f"{hd.get('ngay') or '—'}.")
    if any(t.get(k) is not None for k in ("chua_thue", "thue", "thanh_toan")):
        ra.append(f"\nTiền hàng {_tien(t.get('chua_thue'))} + thuế {_tien(t.get('thue'))} = thanh toán "
                  f"{_tien(t.get('thanh_toan'))}; "
                  f"danh mục cộng {_tien(sum((d['tt'] for d in dm['dong'] if d.get('tt')), Decimal(0)))}.")
    pt = kiem_phep_tinh(hd)
    ra.append("\n## Phép tính trên hóa đơn\n" + ("\n".join(f"- ❌ {x}" for x in pt) if pt else "- ✅ đúng"))
    ra.append(f"\n## Kết quả\n- Hóa đơn {len(hd['dong'])} dòng, danh mục {len(dm['dong'])} dòng; ghép được "
              f"{len(cap)} cặp, {len({x[0] for x in lech})} mặt hàng có lệch.")
    if gom_thue is not None:
        ra.append(f"\n💡 Mọi đơn giá trong danh mục đều bằng giá hóa đơn × {_tien(1 + gom_thue)} — danh mục ghi giá ĐÃ GỒM "
                  f"thuế GTGT {round(gom_thue * 100)}%, hóa đơn ghi giá CHƯA thuế. Nếu đúng ý đó thì không có lệch "
                  "nào khác về giá; bảng dưới chỉ để đối chiếu từng dòng.")
    if lech:
        ra.append("\n| Mặt hàng | Trường | Hóa đơn | Danh mục |\n|---|---|---|---|")
        ra += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in lech]
    if chi_hd:
        ra.append("\n**Chỉ có trên hóa đơn:** " + _liet_ke([hd["dong"][i]["ten"] for i in chi_hd]))
    if chi_dm:
        ra.append("\n**Chỉ có trong danh mục:** " + _liet_ke([dm["dong"][j]["ten"] for j in chi_dm]))
    if not (pt or lech or chi_hd or chi_dm):
        ra.append("\n✅ Hóa đơn khớp danh mục.")
    return _cat_dau_ra("\n".join(ra))


# ── Tuần tự hoá an toàn (Decimal → chuỗi, không qua float) ──────────────────
def _ra_json(o: Any) -> Any:
    if isinstance(o, Decimal):
        return _so_chu(o)
    if isinstance(o, dict):
        return {k: _ra_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_ra_json(v) for v in o]
    return o


# ── Lớp Module ──────────────────────────────────────────────────────────────
_MAU_THAM_SO_DOC = {
    "type": "object",
    "properties": {
        "xml": {"type": "string", "description": "Nội dung hoá đơn điện tử XML (bản gốc pháp lý)."},
        "van_ban_pdf": {"type": "string",
                        "description": "Văn bản Markdown ĐÃ TRÍCH từ PDF (module van-phong trích); "
                                       "dùng khi không có XML."},
        "ten_hd": {"type": "string", "description": "Tên hiển thị của hoá đơn (tuỳ chọn)."},
    },
    "additionalProperties": False,
}

_MAU_THAM_SO_DOI_CHIEU = {
    "type": "object",
    "properties": {
        "xml": {"type": "string", "description": "Nội dung hoá đơn điện tử XML."},
        "van_ban_pdf": {"type": "string", "description": "Văn bản Markdown đã trích từ PDF."},
        "danh_muc": {"type": "array",
                     "description": "Danh mục đã đọc: mảng các dòng, mỗi dòng là mảng ô; "
                                    "dòng đầu là tiêu đề (tên hàng, số lượng, đơn giá, thành tiền)."},
        "xlsx_base64": {"type": "string",
                        "description": "Tệp .xlsx dạng base64 — module đọc bằng zipfile với trần "
                                       "giải nén; công thức không thực thi."},
        "ten_hd": {"type": "string", "description": "Tên hiển thị của hoá đơn (tuỳ chọn)."},
        "ten_dm": {"type": "string", "description": "Tên hiển thị của danh mục (tuỳ chọn)."},
    },
    "additionalProperties": False,
}


class Module:
    """Module hoa-don: hai công cụ doc_hoa_don / doi_chieu_hoa_don (rủi ro «doc»).

    Thuần tính toán trong tiến trình lõi: KHÔNG gọi mạng, không khoá, không lưu dữ liệu.
    Đầu vào (XML/bảng) là DỮ LIỆU KHÔNG TIN — mọi trần bảo vệ ở trên.
    """

    def __init__(self, manifest: dict, _goc_kiem_thu: str | None = None):
        self.manifest = manifest or {}
        self.tien_to = str(self.manifest.get("tien_to") or "hoa_don")

    # ── vòng đời ────────────────────────────────────────────────────────────
    def nap(self, cau_hinh: dict | None = None) -> None:
        return None

    async def cai(self, cau_hinh: dict | None = None) -> dict:
        return {"ok": True, "loi": None, "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}

    async def suc_khoe(self) -> dict:
        return {"ok": True, "che_do": "ngoai_tuyen_khong_goi_mang"}

    async def dung(self) -> None:
        return None

    # ── thực thể / hành động ────────────────────────────────────────────────
    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        return [{
            "id": f"{self.tien_to}.cong_cu",
            "ten": "Công cụ đối chiếu hoá đơn điện tử (XML TT78) với bảng danh mục",
            "loai": "cong_cu",
            "thuoc_tinh": {},
            "tinh_nang": [
                {"hanh_dong": "doc_hoa_don", "rui_ro": "doc", "tham_so": _MAU_THAM_SO_DOC,
                 "mo_ta": "Đọc hoá đơn điện tử XML (bản gốc pháp lý NĐ 123/2020 + TT 78/2021) hoặc văn bản "
                          "Markdown đã trích từ PDF, trả cấu trúc từng dòng hàng và tổng tiền. Thuần đọc."},
                {"hanh_dong": "doi_chieu_hoa_don", "rui_ro": "doc", "tham_so": _MAU_THAM_SO_DOI_CHIEU,
                 "mo_ta": "Đối chiếu hoá đơn với bảng danh mục (mảng dòng hoặc .xlsx base64), báo dòng khớp/"
                          "lệch (số lượng, đơn giá, thành tiền), tự kiểm phép tính trên hoá đơn. Thuần đọc."},
            ],
            "rui_ro_mac_dinh": "doc",
        }]

    async def trang_thai(self, ids) -> dict:
        """Trạng thái THẬT tại thời điểm gọi (hợp đồng C1): module thuần
        tính toán trong bộ nhớ, không giữ trạng thái ngoài — luôn sẵn sàng
        khi đã được nạp."""
        luc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {str(i): {"gia_tri": "san_sang", "luc": luc} for i in (ids or [])}

    def _lay_hoa_don(self, tham_so: dict, *, ten_goi_y: list[str] | None = None) -> dict:
        """Lấy hoá đơn từ xml (ưu tiên) hoặc van_ban_pdf; lỗi → {"loi": ...}. ``ten_goi_y`` là
        tên hàng của danh mục — dùng cho đường neo-theo-tên khi bảng PDF trích lệch cột
        (khớp c2a `doi_chieu_tep`: đọc danh mục trước, hoá đơn sau với gợi ý)."""
        xml = tham_so.get("xml")
        pdf = tham_so.get("van_ban_pdf")
        if xml is not None:
            if not isinstance(xml, str):
                return {"loi": "tham số «xml» phải là chuỗi nội dung XML"}
            return doc_xml(xml)
        if pdf is not None:
            if not isinstance(pdf, str):
                return {"loi": "tham số «van_ban_pdf» phải là chuỗi văn bản Markdown"}
            if len(pdf) > TRAN_MD:
                return {"loi": f"văn bản PDF {len(pdf)} ký tự vượt trần {TRAN_MD}"}
            return doc_tu_markdown(pdf, ten_goi_y)
        return {"loi": "cần «xml» (nội dung hoá đơn XML) hoặc «van_ban_pdf» (văn bản đã trích từ PDF)"}

    def _lay_danh_muc(self, tham_so: dict) -> dict:
        """Lấy danh mục từ mảng dòng đã đọc hoặc .xlsx base64; lỗi → {"loi": ...}."""
        if tham_so.get("danh_muc") is not None:
            return doc_bang_tu_hang(tham_so["danh_muc"])
        b64 = tham_so.get("xlsx_base64")
        if b64 is not None:
            if not isinstance(b64, str):
                return {"loi": "tham số «xlsx_base64» phải là chuỗi base64 của tệp .xlsx"}
            try:
                data = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                return {"loi": "«xlsx_base64» không phải base64 hợp lệ"}
            hang = doc_xlsx_hang(data)          # giữ Decimal thô cho doi_chieu
            if isinstance(hang, dict):
                return hang
            return doc_bang_tu_hang(hang)
        return {"loi": "cần «danh_muc» (mảng các dòng) hoặc «xlsx_base64» (tệp .xlsx dạng base64)"}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = tham_so or {}
        if not isinstance(tham_so, dict):
            return {"ok": False, "loi": "tham_so_sai_kieu"}
        muc = {e["id"]: e for e in await self.thuc_the()}.get(id)
        if muc is None:
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        if hanh_dong not in {"doc_hoa_don", "doi_chieu_hoa_don"}:
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        try:
            if hanh_dong == "doc_hoa_don":
                hd = self._lay_hoa_don(tham_so)
                if hd.get("loi"):
                    return {"ok": False, "loi": hd["loi"]}
                return {"ok": True, "du_lieu": _ra_json(hd), "nguon_ngoai": True}
            # doi_chieu_hoa_don: đọc danh mục TRƯỚC để lấy gợi ý tên cho đường neo PDF (khớp c2a).
            dm = self._lay_danh_muc(tham_so)
            if dm.get("loi"):
                return {"ok": False, "loi": dm["loi"]}
            hd = self._lay_hoa_don(tham_so, ten_goi_y=[d["ten"] for d in dm["dong"]])
            if hd.get("loi"):
                return {"ok": False, "loi": hd["loi"]}
            ten_hd = str(tham_so.get("ten_hd") or "")
            ten_dm = str(tham_so.get("ten_dm") or "")
            # Đối chiếu là việc CPU thuần (ghép/so khớp bảng lớn) — chạy NGOÀI vòng lặp
            # sự kiện để không chặn mọi yêu cầu khác của lõi (soát độc lập B, E3).
            bao_cao = await asyncio.to_thread(doi_chieu, hd, dm, ten_hd=ten_hd, ten_dm=ten_dm)
            return {"ok": True, "bao_cao": bao_cao, "nguon_ngoai": True}
        except LoiDuLieu as exc:
            return {"ok": False, "loi": str(exc)}
        except Exception:
            return {"ok": False, "loi": "loi_noi_bo_khi_xu_ly"}
