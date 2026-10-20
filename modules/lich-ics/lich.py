"""Phân tích và mở rộng ICS cho module lich-ics.

NGUỒN PORT (LUẬT PORT — chép mã đang chạy thật, chỉ đổi lớp kết nối/khuôn module):
`services/calendar_connector.py` CỦA CHÍNH CHỦ MÁY — bản sao tại
/home/hatch/workspace/c2a-nv146/services/calendar_connector.py
(bản sao của /opt/claude-c2a/chatgpt2api, commit d879b03) — giữ nguyên thuật toán
phân tích: `_unfold` (gấp dòng RFC 5545), `_parse_dt` (dạng ngày), `_parse_events`
(cấu trúc sự kiện, trần 200/120/500 ký tự), bộ đệm theo lịch, ý tưởng kiểm tra link
của `test_calendar`.

ĐÃ CHỈNH khi port (ghi rõ để điều phối soát):
1. `_parse_dt` của c2a BỎ QUA tham số TZID và gán múi giờ mặc định, còn xoá «Z» rồi
   coi UTC là giờ địa phương → port này tôn trọng TZID (zoneinfo), giữ «Z» = UTC thật.
2. c2a KHÔNG mở rộng RRULE (sự kiện lặp chỉ hiện đúng lần đầu) → thêm `mo_rong()` có
   trần: tối đa 500 mốc/sự kiện; FREQ dưới DAILY (SECONDLY/MINUTELY/HOURLY — «lặp mỗi
   giây») không được mở rộng, coi như sự kiện đơn.
3. Thêm trần kích thước tệp (2 MiB) và trần số sự kiện (5000/tệp) — ICS là dữ liệu KHÔNG TIN.
4. Tải qua httpx; mỗi bước chuyển hướng kiểm lại bằng ĐÚNG bộ kiểm `app.tu_xa`
   (không viết bộ kiểm thứ hai) — c2a dùng urllib trực tiếp, không kiểm.
5. Lọc ký tự điều khiển trong tiêu đề/địa điểm/mô tả (chống XSS khi hiển thị).

KHÔNG PORT: hệ thông báo của c2a (`notify_targets`, `check_new`, `check_reminders`,
`digest`, `khop_muc_tieu` — gắn kênh Telegram/Zalo riêng của c2a), `prompt_block`
(super_context), cấu hình một-lịch cũ, vòng lặp nền (web2api có `hoi_vong` của lõi
nếu sau này cần nhắc; việc này CHỈ ĐỌC + trả công cụ cho trợ lý).

VÒNG SỬA SAU SOÁT ĐỘC LẬP (báo cáo B mục 4, C1–C5):
- C1: `_sinh_moc` cũ sinh VÔ HẠN (MONTHLY ngày 31 từ tháng 2 → không bao giờ
  yield → treo cả tiến trình) và để OverflowError lọt ra với INTERVAL cực lớn.
  Nay: trần cứng số KỲ sinh (`TRAN_VONG_SINH`), mọi phép số học được bọc lỗi,
  lỗi của MỘT sự kiện chỉ bỏ sự kiện đó (`gom_moc` đếm `bi_loi`).
- C3: mở rộng cũ đếm 500 mốc TỪ DTSTART (sự kiện cũ biến mất khỏi cửa sổ xem),
  không xử lý RECURRENCE-ID, bỏ qua lặng lẽ BYMONTH/BYDAY-thứ-tự/BYSETPOS và
  hiểu sai UNTIL chỉ-ngày. Nay: nhảy thẳng tới cửa sổ bằng số học kỳ + đếm
  chính xác số mốc trước cửa sổ (áp đúng COUNT), RECURRENCE-ID loại buổi gốc,
  hỗ trợ BYMONTH/BYMONTHDAY-âm/BYDAY-thứ-tự/BYSETPOS; thành phần thật sự chưa
  hỗ trợ (BYHOUR, BYWEEKNO, FREQ dưới DAILY…) → sự kiện mang cờ `lap_phuc_tap`
  và kết quả công cụ nói rõ, KHÔNG tạo ngày sai lặng lẽ.
- C5: trần TỔNG số mốc cho một lần mở rộng nhiều sự kiện (`TRAN_TONG_MOC`) —
  chi phí một lần gọi bị chặn bất kể tệp có bao nhiêu sự kiện ác ý.
- C4: tải ICS nay GHIM IP lúc nối bằng `TransportGhimIp` của lõi (phân giải +
  kiểm IP ngay trước khi nối) — hết cửa sổ TOCTOU giữa `kiem_url_lan` và lúc
  httpx tự phân giải lại như trước.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

log = logging.getLogger("web2api.lich_ics")

#: Trần bảo mật cho ICS (dữ liệu KHÔNG TIN từ máy chủ lịch).
TRAN_TEP_BYTES = 2 * 1024 * 1024          #: tệp ICS tối đa 2 MiB
TRAN_SU_KIEN_TEP = 5000                  #: số VEVENT tối đa trong một tệp
TRAN_LAP_LAI = 500                       #: số mốc tối đa khi mở rộng một RRULE
TRAN_VONG_SINH = 20_000                  #: trần số KỲ sinh trong một lần mở rộng (chống treo)
TRAN_TONG_MOC = 20_000                   #: trần TỔNG số mốc sinh trong MỘT lần gọi (mọi sự kiện)
TRAN_DUYET_NHE = 60_000                  #: trần số kỳ duyệt nhẹ để đếm mốc trước cửa sổ
TRAN_TIEU_DE = 200                       #: ký tự tối đa của tiêu đề (theo c2a)
TRAN_DIA_DIEM = 120                      #: ký tự tối đa của địa điểm (theo c2a)
TRAN_MO_TA = 500                         #: ký tự tối đa của mô tả (theo c2a)
TRAN_CHUYEN_HUONG = 5                    #: số bước chuyển hướng tối đa
HET_GIO_TAI = 25.0                       #: hết giờ tải một lịch (theo c2a)

UA = "web2api-lich-ics/1.0"


class LoiLich(Exception):
    """Lỗi đọc lịch mang mã ngắn; KHÔNG BAO GIỜ chứa URL hay nội dung thô."""

    def __init__(self, ma: str, chi_tiet: str = ""):
        super().__init__(ma)
        self.ma = ma          # mã ngắn: tep_qua_lon, mang_loi, het_gio, khong_phai_ics, ...
        self.chi_tiet = chi_tiet  # câu nói thêm, không chứa bí mật


# ── Lọc ký tự điều khiển ────────────────────────────────────────────────

_RE_DIEU_KHIEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def lam_sach(chuoi, tran: int) -> str:
    """Làm sạch một trường chữ từ ICS: bỏ escape RFC 5545, lọc ký tự điều khiển,
    cắt ở trần. Ký tự «<» «>» được GIỮ NGUYÊN (dữ liệu, vận chuyển bằng JSON)."""
    s = chuoi or ""
    if not isinstance(s, str):
        s = str(s)
    s = s.replace("\\N", " ").replace("\\n", " ").replace("\\,", ",").replace("\\;", ";")
    s = s.replace("\\\\", "\\")
    s = _RE_DIEU_KHIEN.sub("", s)
    return s[:tran].strip()


# ── Phân tích ngày giờ ──────────────────────────────────────────────────

_MAU_DT = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(?:(\d{2}))?)?$")
_MAU_NGAY = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_MAU_DURATION = re.compile(
    r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _tz(theo_ten: str, mac_dinh: ZoneInfo) -> ZoneInfo:
    try:
        return ZoneInfo(theo_ten)
    except Exception:
        return mac_dinh


def phan_tich_dt(dong: str, mui_gio: ZoneInfo) -> tuple:
    """Phân tích MỘT dòng ngày giờ đầy đủ (cả tham số) → (datetime aware, cả_ngày?).

    Dòng dạng «DTSTART;TZID=America/New_York:20260306T090000» — tham số nằm giữa
    tên thuộc tính và dấu «:» đầu tiên. «Z» cuối = UTC thật. VALUE=DATE hoặc dạng
    ngày thuần (không có «T») = sự kiện cả ngày, 00:00 theo múi giờ nhà.
    TZID lạ → về múi giờ mặc định (không vỡ). Sai khuôn → (None, False)."""
    if not dong or ":" not in dong:
        return None, False
    dau, gia_tri = dong.split(":", 1)
    gia_tri = gia_tri.strip()
    tham_so: dict = {}
    for phan in dau.split(";")[1:]:
        if "=" in phan:
            k, v = phan.split("=", 1)
            tham_so[k.strip().upper()] = v.strip()
    la_ngay = tham_so.get("VALUE", "").upper() == "DATE" or "T" not in gia_tri
    if la_ngay:
        m = _MAU_NGAY.match(gia_tri)
        if not m:
            return None, False
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=mui_gio), True
        except ValueError:
            return None, False
    utc = gia_tri.endswith(("Z", "z"))
    cot = gia_tri[:-1] if utc else gia_tri
    m = _MAU_DT.match(cot)
    if not m or m[4] is None:
        return None, False
    try:
        dt = datetime(int(m[1]), int(m[2]), int(m[3]),
                     int(m[4]), int(m[5]), int(m[6]) if m[6] else 0)
    except ValueError:
        return None, False
    if utc:
        return dt.replace(tzinfo=timezone.utc), False
    tzid = tham_so.get("TZID", "")
    return dt.replace(tzinfo=_tz(tzid, mui_gio) if tzid else mui_gio), False


def phan_tich_duration(chuoi: str):
    """DURATION ISO 8601 rút gọn (P[n]W / P[n]DT[n]H[n]M[n]S) → timedelta; hỏng → None."""
    m = _MAU_DURATION.match((chuoi or "").strip().upper())
    if not m:
        return None
    tuan, ngay, gio, phut, giay = (int(x) if x else 0 for x in m.groups())
    td = timedelta(weeks=tuan, days=ngay, hours=gio, minutes=phut, seconds=giay)
    return td if td > timedelta(0) else None


# ── Phân tích VEVENT ────────────────────────────────────────────────────

def _giai_dong(ics: str) -> str:
    """RFC 5545: dòng gấp = CRLF + space/tab → nối lại (giữ nguyên từ c2a)."""
    return re.sub(r"\r?\n[ \t]", "", ics or "")


def phan_tich_ics(noi_dung: str, mui_gio: ZoneInfo) -> list:
    """Phân tích tệp ICS → danh sách sự kiện gốc (chưa mở rộng RRULE).

    Mỗi sự kiện: {uid, tieu_de, bat_dau, ket_thuc, ca_ngay, dia_diem, mo_ta,
    rrule_dict (None nếu không lặp/không mở rộng được), lap_phuc_tap (True nếu
    có RRULE nhưng không mở rộng được — FREQ lạ/thành phần chưa hỗ trợ — để
    tầng công cụ nói rõ thay vì tạo ngày sai), recurrence_id, exdate (set
    datetime)}. Một bản ghi RECURRENCE-ID (buổi bị dời/xoá) loại buổi gốc tương
    ứng khỏi sự kiện chính cùng UID (RANGE=THISANDFUTURE chưa hỗ trợ).
    Vượt trần → LoiLich(tep_qua_lon / qua_nhieu_su_kien)."""
    if len(noi_dung.encode("utf-8", "replace")) > TRAN_TEP_BYTES:
        raise LoiLich("tep_qua_lon")
    ics = _giai_dong(noi_dung)
    khoi = re.split(r"BEGIN:VEVENT", ics, flags=re.I)[1:]
    if len(khoi) > TRAN_SU_KIEN_TEP:
        raise LoiLich("qua_nhieu_su_kien")
    su_kien = []
    for b in khoi:
        end = re.split(r"END:VEVENT", b, flags=re.I)[0]
        dong_dtstart = dong_dtend = dong_recurrence = None
        dong_exdate: list = []
        rrule_tho = None
        truong: dict = {}
        for dong in end.splitlines():
            if ":" not in dong:
                continue
            ten_day, gia_tri = dong.split(":", 1)
            ten = ten_day.split(";")[0].strip().upper()
            if ten == "DTSTART":
                dong_dtstart = dong.strip()
            elif ten == "DTEND":
                dong_dtend = dong.strip()
            elif ten == "RECURRENCE-ID":
                dong_recurrence = dong.strip()
            elif ten == "EXDATE":
                dong_exdate.append(dong.strip())
            elif ten == "RRULE" and rrule_tho is None:
                rrule_tho = gia_tri.strip()
            elif ten not in truong:
                truong[ten] = gia_tri.strip()
        if not dong_dtstart:
            continue
        bat_dau, ca_ngay = phan_tich_dt(dong_dtstart, mui_gio)
        if bat_dau is None:
            continue
        ket_thuc = None
        if dong_dtend:
            ket_thuc, _ = phan_tich_dt(dong_dtend, mui_gio)
        if ket_thuc is None and "DURATION" in truong:
            dai = phan_tich_duration(truong["DURATION"])
            if dai:
                ket_thuc = bat_dau + dai
        exdate = set()
        for dong_ex in dong_exdate:
            dau_ex, _ = dong_ex.split(":", 1)
            for gia_tri_ex in dong_ex.split(":", 1)[1].split(","):
                d2, _ = phan_tich_dt(dau_ex + ":" + gia_tri_ex.strip(), mui_gio)
                if d2 is not None:
                    exdate.add(d2)
        rrule_dict, lap_phuc_tap = (
            _phan_tich_rrule(rrule_tho) if rrule_tho else (None, False))
        recurrence_id = None
        if dong_recurrence:
            recurrence_id, _ = phan_tich_dt(dong_recurrence, mui_gio)
        su_kien.append({
            "uid": lam_sach(truong.get("UID", ""), 200),
            "tieu_de": lam_sach(truong.get("SUMMARY") or "(không tiêu đề)", TRAN_TIEU_DE),
            "bat_dau": bat_dau,
            "ket_thuc": ket_thuc,
            "ca_ngay": ca_ngay,
            "dia_diem": lam_sach(truong.get("LOCATION", ""), TRAN_DIA_DIEM),
            "mo_ta": lam_sach(truong.get("DESCRIPTION", ""), TRAN_MO_TA),
            "rrule_dict": rrule_dict,
            "lap_phuc_tap": bool(lap_phuc_tap),
            "recurrence_id": recurrence_id,
            "exdate": exdate,
        })
    # RECURRENCE-ID: bản ghi dời/xoá một buổi → loại buổi gốc khỏi sự kiện
    # chính cùng UID (không để hiện cả gốc lẫn bản dời).
    chinh_theo_uid: dict = {}
    for e in su_kien:
        if e.get("recurrence_id") is None and e.get("uid"):
            chinh_theo_uid.setdefault(e["uid"], []).append(e)
    for e in su_kien:
        rid = e.get("recurrence_id")
        if rid is not None and e.get("uid") in chinh_theo_uid:
            for chinh in chinh_theo_uid[e["uid"]]:
                chinh["exdate"].add(rid)
    su_kien.sort(key=lambda e: e["bat_dau"])
    return su_kien


# ── Mở rộng RRULE (có trần, nhảy cửa sổ) ────────────────────────────────

_TUAN = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

#: Thành phần RRULE chưa hỗ trợ — gặp là đánh dấu «phức tạp», không đoán bừa.
_CHUA_HO_TRO = ("BYHOUR", "BYMINUTE", "BYSECOND", "BYWEEKNO", "BYYEARDAY",
                "BYEASTER")


def _phan_tich_rrule(chuoi: str):
    """RRULE → (dict | None, phuc_tap).

    dict = quy tắc mở rộng được; None + phuc_tap=True = có RRULE nhưng không
    mở rộng được (FREQ ngoài DAILY/WEEKLY/MONTHLY/YEARLY — gồm «lặp mỗi giây»;
    thành phần chưa hỗ trợ như BYHOUR/BYWEEKNO; BYDAY có thứ tự đặt sai FREQ;
    giá trị hỏng) → tầng trên chỉ hiện lần đầu và NÓI RÕ cho người dùng.
    Hỗ trợ: INTERVAL/COUNT/UNTIL (kể cả UNTIL chỉ ngày), BYMONTH, BYMONTHDAY
    (kể cả số âm = đếm từ cuối tháng), BYDAY (thuần = lọc/mọi ngày đó; có thứ
    tự «1MO»/«-1FR» cho MONTHLY và YEARLY), BYSETPOS cho MONTHLY/YEARLY.
    WKST không ảnh hưởng tập mốc với cách neo tuần theo DTSTART → bỏ qua."""
    try:
        phan: dict = {}
        for muc in chuoi.split(";"):
            if "=" not in muc:
                return None, True
            k, v = muc.split("=", 1)
            phan[k.strip().upper()] = v.strip().upper()
    except AttributeError:
        return None, True
    if any(k in phan for k in _CHUA_HO_TRO):
        return None, True
    freq = phan.get("FREQ", "")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return None, True
    rr: dict = {"freq": freq, "interval": 1}
    try:
        if "INTERVAL" in phan:
            rr["interval"] = int(phan["INTERVAL"])
            if rr["interval"] < 1:
                return None, True
        if "COUNT" in phan:
            rr["count"] = int(phan["COUNT"])
            if rr["count"] < 1:
                return None, True
        if "BYMONTH" in phan:
            thang = [int(x) for x in phan["BYMONTH"].split(",")]
            if not all(1 <= x <= 12 for x in thang):
                return None, True
            rr["bymonth"] = sorted(set(thang))
        if "BYMONTHDAY" in phan:
            ngay = [int(x) for x in phan["BYMONTHDAY"].split(",")]
            if not all(1 <= abs(x) <= 31 for x in ngay):
                return None, True
            rr["bymonthday"] = sorted(set(ngay))
        if "BYSETPOS" in phan:
            if freq not in ("MONTHLY", "YEARLY"):
                return None, True
            pos = [int(x) for x in phan["BYSETPOS"].split(",")]
            if not all(1 <= abs(x) <= 366 for x in pos):
                return None, True
            rr["bysetpos"] = sorted(set(pos))
    except ValueError:
        return None, True
    if "UNTIL" in phan:
        rr["until_tho"] = phan["UNTIL"]
    if "BYDAY" in phan:
        thuan, thu_tu = [], []
        for d in phan["BYDAY"].split(","):
            m = re.match(r"^([+-]?\d{1,2})?([A-Z]{2})$", d.strip())
            if not m or m.group(2) not in _TUAN:
                return None, True
            if m.group(1):
                n = int(m.group(1))
                if n == 0 or abs(n) > 53:
                    return None, True
                thu_tu.append((_TUAN[m.group(2)], n))
            else:
                thuan.append(m.group(2))
        if thuan:
            rr["byday"] = sorted(set(thuan), key=_TUAN.get)
        if thu_tu:
            rr["byday_thu_tu"] = thu_tu
    # Ràng buộc theo FREQ (đặt sai vị trí = không đoán, đánh dấu phức tạp):
    if freq in ("DAILY", "WEEKLY") and "byday_thu_tu" in rr:
        return None, True
    if freq == "YEARLY" and "bymonth" not in rr:
        # BYDAY thuần trong YEARLY không kèm BYMONTH = «mọi thứ X của năm» —
        # chưa hỗ trợ; BYDAY thứ tự kèm BYMONTHDAY (không BYMONTH) cũng chưa.
        if "byday" in rr:
            return None, True
        if "byday_thu_tu" in rr and "bymonthday" in rr:
            return None, True
    return rr, False


def _ngay_cua_thang(nam: int, thang: int, ngay: int, gio: tuple, tz):
    """datetime(nam, thang, ngay, *gio) — ngày không tồn tại (30/2) → None."""
    try:
        return datetime(nam, thang, ngay, *gio, tzinfo=tz)
    except ValueError:
        return None


def _khop_bymonthday(m: datetime, ds: list) -> bool:
    so_ngay = monthrange(m.year, m.month)[1]
    for d in ds:
        if m.day == (d if d > 0 else so_ngay + d + 1):
            return True
    return False


def _loc_moc(m: datetime, rr: dict, *, bo_qua_byday: bool = False) -> bool:
    """Áp các thành phần BY dạng LỌC lên một mốc ứng viên (DAILY/WEEKLY)."""
    if rr.get("bymonth") and m.month not in rr["bymonth"]:
        return False
    if rr.get("bymonthday") and not _khop_bymonthday(m, rr["bymonthday"]):
        return False
    if not bo_qua_byday and rr.get("byday"):
        if m.weekday() not in {_TUAN[t] for t in rr["byday"]}:
            return False
    return True


def _ngay_thu_n_trong_thang(nam: int, thang: int, wd: int, n: int,
                            so_ngay: int):
    """Ngày (1..cuối tháng) là thứ `wd` thứ `n` của tháng; n<0 đếm từ cuối.
    Không tồn tại (thứ 5 của tháng chỉ có 4) → None."""
    if n > 0:
        ng = 1 + (wd - date(nam, thang, 1).weekday()) % 7 + (n - 1) * 7
        return ng if ng <= so_ngay else None
    ng = so_ngay - (date(nam, thang, so_ngay).weekday() - wd) % 7 + (n + 1) * 7
    return ng if ng >= 1 else None


def _ngay_thu_n_trong_nam(nam: int, wd: int, n: int):
    """date là thứ `wd` thứ `n` của NĂM (YEARLY không BYMONTH); không có → None."""
    if n > 0:
        d = date(nam, 1, 1) + timedelta(days=(wd - date(nam, 1, 1).weekday()) % 7
                                        + (n - 1) * 7)
    else:
        d = date(nam, 12, 31) - timedelta(
            days=(date(nam, 12, 31).weekday() - wd) % 7 + (-n - 1) * 7)
    return d if d.year == nam else None


def _ap_bysetpos(ds: list, rr: dict) -> list:
    pos = rr.get("bysetpos")
    if not pos or not ds:
        return ds
    chon = set()
    for p in pos:
        i = p - 1 if p > 0 else len(ds) + p
        if 0 <= i < len(ds):
            chon.add(i)
    return [ds[i] for i in sorted(chon)]


def _ung_vien_trong_thang(nam: int, thang: int, rr: dict, bat_dau: datetime,
                          *, ap_bysetpos: bool) -> list:
    """Mọi mốc ứng viên của MỘT tháng theo BYMONTHDAY/BYDAY (đã sắp tăng dần).
    Tháng ngoài BYMONTH → []. Không có quy tắc ngày nào → ngày của DTSTART.
    Chưa lọc `>= bat_dau` (người gọi lo)."""
    if rr.get("bymonth") and thang not in rr["bymonth"]:
        return []
    try:
        so_ngay = monthrange(nam, thang)[1]
    except ValueError:
        return []
    ngay: set = set()
    co_quy_tac = False
    if "bymonthday" in rr:
        co_quy_tac = True
        for d in rr["bymonthday"]:
            ng = d if d > 0 else so_ngay + d + 1
            if 1 <= ng <= so_ngay:
                ngay.add(ng)
    if "byday_thu_tu" in rr:
        co_quy_tac = True
        for wd, n in rr["byday_thu_tu"]:
            ng = _ngay_thu_n_trong_thang(nam, thang, wd, n, so_ngay)
            if ng is not None:
                ngay.add(ng)
    if "byday" in rr:
        co_quy_tac = True
        for ten in rr["byday"]:
            wd = _TUAN[ten]
            ng = 1 + (wd - date(nam, thang, 1).weekday()) % 7
            while ng <= so_ngay:
                ngay.add(ng)
                ng += 7
    if not co_quy_tac and bat_dau.day <= so_ngay:
        ngay.add(bat_dau.day)
    ds = [datetime(nam, thang, ng, bat_dau.hour, bat_dau.minute,
                   bat_dau.second, tzinfo=bat_dau.tzinfo)
          for ng in sorted(ngay)]
    return _ap_bysetpos(ds, rr) if ap_bysetpos else ds


def _ung_vien_trong_nam(nam: int, rr: dict, bat_dau: datetime) -> list:
    """Mọi mốc ứng viên của MỘT năm (đã sắp). BYSETPOS áp trên cả năm."""
    if rr.get("bymonth"):
        ds: list = []
        for th in rr["bymonth"]:
            ds.extend(_ung_vien_trong_thang(nam, th, rr, bat_dau,
                                            ap_bysetpos=False))
        ds.sort()
    elif "byday_thu_tu" in rr:
        ds = []
        for wd, n in rr["byday_thu_tu"]:
            d = _ngay_thu_n_trong_nam(nam, wd, n)
            if d is not None:
                ds.append(datetime(nam, d.month, d.day, bat_dau.hour,
                                   bat_dau.minute, bat_dau.second,
                                   tzinfo=bat_dau.tzinfo))
        ds.sort()
    else:
        ds = _ung_vien_trong_thang(nam, bat_dau.month, rr, bat_dau,
                                   ap_bysetpos=False)
    return _ap_bysetpos(ds, rr)


# ── Bộ sinh mốc: nhảy thẳng tới cửa sổ, đếm đúng COUNT ──────────────────
#
# Mỗi hàm _sinh_* yield (moc, chi_so): chi_so là số thứ tự của mốc THÔ tính
# từ DTSTART (đã qua mọi bộ lọc BY, trước EXDATE — đúng cách RFC tính COUNT).
# Khi truyền `tu` (đầu cửa sổ xem), hàm NHẢY bằng số học tới kỳ chứa `tu`
# (lùi thêm một kỳ cho an toàn biên/DST) rồi ĐẾM CHÍNH XÁC số mốc trước đó
# bằng duyệt nhẹ; chi_so = None khi số kỳ cần duyệt vượt TRAN_DUYET_NHE (quá
# cổ) — lúc đó COUNT không áp chính xác được nữa, người gọi cắt an toàn.
# Mọi hàm đều bị chặn bởi TRAN_VONG_SINH kỳ và dừng khi năm vượt 9999 hoặc
# phép số học tràn — RRULE ác ý không treo được tiến trình.

def _sinh_daily(su_kien: dict, rr: dict, tu):
    bat_dau: datetime = su_kien["bat_dau"]
    interval = rr["interval"]
    try:
        buoc = timedelta(days=interval)
    except OverflowError:
        yield bat_dau, 0  # bước nhảy vượt tầm datetime: chỉ còn chính DTSTART
        return
    ky = 0
    chi_so: int | None = 0
    if tu is not None and tu > bat_dau:
        ky = max(0, (tu - bat_dau) // buoc - 1)
        if any(k in rr for k in ("bymonth", "bymonthday", "byday")):
            if ky > TRAN_DUYET_NHE:
                chi_so = None
            else:
                chi_so = 0
                for k in range(ky):
                    try:
                        m0 = bat_dau + k * buoc
                    except OverflowError:
                        break
                    if _loc_moc(m0, rr):
                        chi_so += 1
        else:
            chi_so = ky
    vong = 0
    while vong < TRAN_VONG_SINH:
        vong += 1
        try:
            m = bat_dau + ky * buoc
        except OverflowError:
            return
        ky += 1
        if not _loc_moc(m, rr):
            continue
        yield m, chi_so
        if chi_so is not None:
            chi_so += 1


def _sinh_weekly(su_kien: dict, rr: dict, tu):
    bat_dau: datetime = su_kien["bat_dau"]
    interval = rr["interval"]
    nguoc = {v: k for k, v in _TUAN.items()}
    byday = rr.get("byday") or [nguoc[bat_dau.weekday()]]
    thu_hai0 = bat_dau - timedelta(days=bat_dau.weekday())

    def ung_vien(tuan: int):
        kq = []
        for ten_ngay in byday:
            m = thu_hai0 + timedelta(weeks=tuan, days=_TUAN[ten_ngay])
            if m >= bat_dau and _loc_moc(m, rr, bo_qua_byday=True):
                kq.append(m)
        return kq

    tuan_bd = 0
    chi_so: int | None = 0
    if tu is not None and tu > bat_dau:
        thu_hai_tu = tu - timedelta(days=tu.weekday())
        so_tuan = (thu_hai_tu - thu_hai0).days // 7
        if so_tuan > 0:
            tuan_bd = max(0, (so_tuan // interval) * interval - interval)
            if tuan_bd // interval > TRAN_DUYET_NHE:
                chi_so = None
            else:
                chi_so = 0
                for tuan in range(0, tuan_bd, interval):
                    try:
                        chi_so += len(ung_vien(tuan))
                    except OverflowError:
                        break
    tuan = tuan_bd
    vong = 0
    while vong < TRAN_VONG_SINH:
        vong += 1
        try:
            cac_moc = ung_vien(tuan)
        except OverflowError:
            return
        for m in cac_moc:
            yield m, chi_so
            if chi_so is not None:
                chi_so += 1
        tuan += interval


def _sinh_monthly(su_kien: dict, rr: dict, tu):
    bat_dau: datetime = su_kien["bat_dau"]
    interval = rr["interval"]
    off_bd = 0
    chi_so: int | None = 0
    if tu is not None and tu > bat_dau:
        off_chua = (tu.year - bat_dau.year) * 12 + (tu.month - bat_dau.month)
        if off_chua > 0:
            off_bd = max(0, (off_chua // interval) * interval - interval)
            if off_bd // interval > TRAN_DUYET_NHE:
                chi_so = None
            else:
                chi_so = 0
                for off in range(0, off_bd, interval):
                    tong = (bat_dau.month - 1) + off
                    nam, th = bat_dau.year + tong // 12, tong % 12 + 1
                    if nam > 9999:
                        break
                    chi_so += sum(
                        1 for m in _ung_vien_trong_thang(
                            nam, th, rr, bat_dau, ap_bysetpos=True)
                        if m >= bat_dau)
    off = off_bd
    vong = 0
    while vong < TRAN_VONG_SINH:
        vong += 1
        tong = (bat_dau.month - 1) + off
        nam, th = bat_dau.year + tong // 12, tong % 12 + 1
        if nam > 9999:
            return
        for m in _ung_vien_trong_thang(nam, th, rr, bat_dau, ap_bysetpos=True):
            if m < bat_dau:
                continue
            yield m, chi_so
            if chi_so is not None:
                chi_so += 1
        off += interval


def _sinh_yearly(su_kien: dict, rr: dict, tu):
    bat_dau: datetime = su_kien["bat_dau"]
    interval = rr["interval"]
    off_bd = 0
    chi_so: int | None = 0
    if tu is not None and tu > bat_dau:
        off_chua = tu.year - bat_dau.year
        if off_chua > 0:
            off_bd = max(0, (off_chua // interval) * interval - interval)
            if off_bd // interval > TRAN_DUYET_NHE:
                chi_so = None
            else:
                chi_so = 0
                for off in range(0, off_bd, interval):
                    nam = bat_dau.year + off
                    if nam > 9999:
                        break
                    chi_so += sum(
                        1 for m in _ung_vien_trong_nam(nam, rr, bat_dau)
                        if m >= bat_dau)
    off = off_bd
    vong = 0
    while vong < TRAN_VONG_SINH:
        vong += 1
        nam = bat_dau.year + off
        if nam > 9999:
            return
        for m in _ung_vien_trong_nam(nam, rr, bat_dau):
            if m < bat_dau:
                continue
            yield m, chi_so
            if chi_so is not None:
                chi_so += 1
        off += interval


_SINH_THEO_FREQ = {
    "DAILY": _sinh_daily,
    "WEEKLY": _sinh_weekly,
    "MONTHLY": _sinh_monthly,
    "YEARLY": _sinh_yearly,
}


def _sinh_moc(su_kien: dict, rr: dict, tu=None):
    """Sinh (moc, chi_so) tăng dần theo RRULE — HỮU HẠN nhờ trần kỳ + năm."""
    yield from _SINH_THEO_FREQ[rr["freq"]](su_kien, rr, tu)


def _until_hieu_luc(rr: dict, bat_dau: datetime):
    """UNTIL đã phân tích; dạng CHỈ NGÀY = hết NGÀY đó (23:59:59 theo múi giờ
    của chính giá trị UNTIL), không phải 00:00 — giữ buổi cuối ngày."""
    tho = rr.get("until_tho")
    if not tho:
        return None
    until, ca_ngay = phan_tich_dt("DTSTART:" + tho, bat_dau.tzinfo)
    if until is None:
        return None
    if ca_ngay:
        return until.replace(hour=23, minute=59, second=59,
                             microsecond=999999)
    return until


def mo_rong_chi_tiet(su_kien: dict, den: datetime, tu: datetime = None):
    """Mở rộng một sự kiện → (ds_moc ≤ `den`, cờ {bi_loi, da_cat, da_sinh}).

    - Không RRULE / RRULE phức tạp → đúng 1 mốc (nếu ≤ den, chưa bị loại trừ).
    - `tu` = đầu cửa sổ xem: NHẢY thẳng tới cửa sổ (không đếm từ DTSTART)
      nhưng COUNT vẫn áp đúng nhờ chi_so đếm chính xác số mốc trước cửa sổ.
    - Trần TRAN_LAP_LAI áp trên mốc TRẢ VỀ (sau EXDATE): buổi bị loại trừ
      không ăn mất trần của các buổi thật; chi phí sinh vẫn bị chặn bởi
      TRAN_VONG_SINH ở tầng sinh và ngân sách của `gom_moc` tính theo mốc sinh.
    - COUNT tính trên mốc THÔ (trước EXDATE), đúng RFC 5545. Mốc trả về có
      thể < `tu` (kỳ lùi an toàn) — người gọi lọc lại theo cửa sổ thật.
    - Lỗi số học của chính sự kiện này → bi_loi=True, trả mốc đã sinh được;
      không bao giờ ném ra ngoài."""
    co = {"bi_loi": False, "da_cat": False, "da_sinh": 0}
    bat_dau: datetime = su_kien["bat_dau"]
    loai_tru: set = su_kien.get("exdate") or set()
    rr = su_kien.get("rrule_dict")
    if not rr:
        kq = [bat_dau] if bat_dau <= den and bat_dau not in loai_tru else []
        co["da_sinh"] = len(kq)
        return kq, co
    until = _until_hieu_luc(rr, bat_dau)
    count = rr.get("count")
    kq: list = []
    try:
        for m, chi_so in _sinh_moc(su_kien, rr, tu):
            co["da_sinh"] += 1
            if chi_so is None:
                if count is not None:
                    # Quá cổ để đếm chính xác mà lại có COUNT: cắt an toàn
                    # thay vì đoán (tránh hiện sự kiện đã hết từ lâu).
                    co["da_cat"] = True
                    break
            elif count is not None and chi_so >= count:
                break
            if m > den:
                break
            if until is not None and m > until:
                break
            if m in loai_tru:
                continue
            kq.append(m)
            if len(kq) >= TRAN_LAP_LAI:
                co["da_cat"] = True
                break
    except (OverflowError, ValueError, MemoryError):
        co["bi_loi"] = True
    kq.sort()
    return kq, co


def mo_rong(su_kien: dict, den: datetime, tu: datetime = None) -> list:
    """Mở rộng một sự kiện thành các mốc bắt đầu ≤ `den` (xem `mo_rong_chi_tiet`).

    Giữ chữ ký/trả về như bản đầu cho nơi gọi cũ; `tu` (tuỳ chọn) là đầu cửa
    sổ xem để nhảy thẳng tới thay vì đếm từ DTSTART."""
    kq, _co = mo_rong_chi_tiet(su_kien, den, tu)
    return kq


def gom_moc(ds_su_kien: list, den: datetime, tu: datetime = None,
            tran_tong: int = TRAN_TONG_MOC):
    """Mở rộng NHIỀU sự kiện với NGÂN SÁCH CHUNG cho một lần gọi (C5).

    Trả (bang, thong_ke): bang[i] là mốc của ds_su_kien[i]; thong_ke =
    {bi_loi: số sự kiện lỗi đã bị bỏ, da_cat: có chạm trần nào không,
    tong_moc: tổng số mốc đã sinh (tính vào ngân sách)}. Ngân sách tính trên
    mốc SINH RA (kể cả mốc bị EXDATE loại) vì đó là chi phí CPU thật. Một sự
    kiện lỗi/hỏng chỉ bị bỏ mình nó — không hỏng cả lịch."""
    bang: list = []
    thong_ke = {"bi_loi": 0, "da_cat": False, "tong_moc": 0}
    con_lai = tran_tong
    for sk in ds_su_kien:
        if con_lai <= 0:
            thong_ke["da_cat"] = True
            bang.append([])
            continue
        try:
            moc, co = mo_rong_chi_tiet(sk, den, tu)
        except Exception:  # lưới cuối: sự kiện hỏng cấu trúc không hỏng cả lịch
            thong_ke["bi_loi"] += 1
            bang.append([])
            continue
        if co.get("bi_loi"):
            thong_ke["bi_loi"] += 1
        if co.get("da_cat"):
            thong_ke["da_cat"] = True
        if co["da_sinh"] > con_lai:
            # Sự kiện này một mình vượt ngân sách còn lại → bỏ, cắt tại đây.
            thong_ke["da_cat"] = True
            thong_ke["tong_moc"] += con_lai
            con_lai = 0
            bang.append([])
            continue
        con_lai -= co["da_sinh"]
        thong_ke["tong_moc"] += co["da_sinh"]
        bang.append(moc)
    return bang, thong_ke



# ── Tải ICS (qua đúng bộ kiểm app/tu_xa, không viết bộ kiểm thứ hai) ─────

#: Tên máy của MỌI URL đã tải (gồm đích chuyển hướng) — bộ lọc log của
#: module.py dùng tập này để chặn theo TÊN MÁY ĐÃ CHUẨN HOÁ (C2): URL đích
#: chuyển hướng không nằm trong cấu hình nhưng log httpx vẫn in ra nó.
_HOST_DA_TAI: set = set()
_khoa_host = threading.RLock()


def hosts_da_tai() -> set:
    with _khoa_host:
        return set(_HOST_DA_TAI)


def _ghi_host(url: str) -> None:
    try:
        h = urlsplit(url or "").hostname  # hostname đã thường hoá theo RFC
    except ValueError:
        h = None
    if h:
        with _khoa_host:
            _HOST_DA_TAI.add(h)


async def tai_ics(url: str, *, het_gio: float = HET_GIO_TAI) -> str:
    """Tải nội dung ICS. Mỗi bước chuyển hướng được kiểm lại bằng `kiem_url_lan`
    của lõi, và kết nối thật được GHIM IP bằng `TransportGhimIp` (phân giải +
    kiểm mọi IP ngay trước khi nối) — hết cửa sổ TOCTOU «kiểm một IP, nối một
    IP khác» của bản cũ. Lỗi → LoiLich mang mã ngắn, không chứa URL."""
    import httpx
    from app.chuyen_tiep import LoiDichBiChan, TransportGhimIp
    from app.tu_xa import _dia_chi_bi_chan, kiem_url_lan

    hien_tai = url
    _ghi_host(hien_tai)
    async with httpx.AsyncClient(
            timeout=het_gio, follow_redirects=False,
            transport=TransportGhimIp(_dia_chi_bi_chan)) as khach:
        for _ in range(TRAN_CHUYEN_HUONG + 1):
            try:
                hien_tai = await kiem_url_lan(hien_tai)
            except ValueError as e:
                raise LoiLich("chuyen_huong_bi_chan", str(e)) from None
            _ghi_host(hien_tai)
            try:
                async with khach.stream("GET", hien_tai,
                                        headers={"User-Agent": UA}) as resp:
                    ma = resp.status_code
                    if ma in (301, 302, 303, 307, 308):
                        dich = resp.headers.get("location", "")
                        if not dich:
                            raise LoiLich("mang_loi", "chuyển hướng không có Location")
                        hien_tai = urljoin(hien_tai, dich)
                        _ghi_host(hien_tai)
                        continue
                    if ma == 404:
                        raise LoiLich("khong_thay_404")
                    if ma in (401, 403):
                        raise LoiLich("bi_tu_choi", f"mã {ma}")
                    if ma >= 400:
                        raise LoiLich("mang_loi", f"mã {ma}")
                    tong = 0
                    cac_khuc = []
                    async for khuc in resp.aiter_bytes():
                        tong += len(khuc)
                        if tong > TRAN_TEP_BYTES:
                            raise LoiLich("tep_qua_lon")
                        cac_khuc.append(khuc)
                    noi_dung = b"".join(cac_khuc).decode("utf-8", "replace")
                    if "BEGIN:VCALENDAR" not in noi_dung.upper():
                        raise LoiLich("khong_phai_ics")
                    return noi_dung
            except LoiLich:
                raise
            except LoiDichBiChan:
                raise LoiLich("chuyen_huong_bi_chan",
                              "đích phân giải ra địa chỉ bị chặn lúc kết nối") from None
            except TimeoutError:
                raise LoiLich("het_gio") from None
            except Exception as e:
                ten = type(e).__name__
                if "timeout" in ten.lower() or "timeout" in str(e).lower():
                    raise LoiLich("het_gio") from None
                raise LoiLich("mang_loi", ten) from None
        raise LoiLich("chuyen_huong_bi_chan", "quá nhiều bước chuyển hướng")


# ── Bộ đệm theo lịch ─────────────────────────────────────────────────────

_cache: dict = {}
_khoa_cache = threading.RLock()


def lay_cache(khoa: str):
    with _khoa_cache:
        return (_cache.get(khoa) or {}).get("su_kien")


def dat_cache(khoa: str, su_kien: list) -> None:
    with _khoa_cache:
        _cache[khoa] = {"ts": time.time(), "su_kien": su_kien}


def cache_con_han(khoa: str, han_giay: int) -> bool:
    with _khoa_cache:
        c = _cache.get(khoa) or {}
        return bool(c.get("su_kien")) and (time.time() - float(c.get("ts") or 0) < han_giay)


def xoa_cache() -> None:
    """Chỉ dùng cho test."""
    with _khoa_cache:
        _cache.clear()


def giu_cache(giu: set) -> None:
    """Bỏ bản đệm của các lịch KHÔNG còn trong cấu hình (khi `nap` lại).

    Bản đệm của lịch còn dùng được GIỮ NGUYÊN — `cai()` đã tải thử và làm ấm
    bộ đệm, `nap()` ngay sau đó không được ép tải lại."""
    with _khoa_cache:
        for khoa in [k for k in _cache if k not in giu]:
            del _cache[khoa]


# ── Tiện ích trình bày ──────────────────────────────────────────────────

def dinh_dang_moc(su_kien: dict, moc: datetime) -> dict:
    """Một mốc lặp thành bản ghi JSON cho công cụ: giữ nguyên thời lượng gốc."""
    bat_dau = su_kien["bat_dau"]
    ket_thuc = su_kien.get("ket_thuc")
    kt = (moc + (ket_thuc - bat_dau)) if ket_thuc else None
    return {
        "tieu_de": su_kien["tieu_de"],
        "bat_dau": moc.isoformat(),
        "ket_thuc": kt.isoformat() if kt else None,
        "ca_ngay": bool(su_kien.get("ca_ngay")),
        "dia_diem": su_kien.get("dia_diem") or "",
        "mo_ta": su_kien.get("mo_ta") or "",
    }
