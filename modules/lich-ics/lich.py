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
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

log = logging.getLogger("web2api.lich_ics")

#: Trần bảo mật cho ICS (dữ liệu KHÔNG TIN từ máy chủ lịch).
TRAN_TEP_BYTES = 2 * 1024 * 1024          #: tệp ICS tối đa 2 MiB
TRAN_SU_KIEN_TEP = 5000                  #: số VEVENT tối đa trong một tệp
TRAN_LAP_LAI = 500                       #: số mốc tối đa khi mở rộng một RRULE
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
    rrule_dict (None nếu không lặp/không mở rộng được), exdate (set datetime)}.
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
        dong_dtstart = dong_dtend = None
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
        su_kien.append({
            "uid": lam_sach(truong.get("UID", ""), 200),
            "tieu_de": lam_sach(truong.get("SUMMARY") or "(không tiêu đề)", TRAN_TIEU_DE),
            "bat_dau": bat_dau,
            "ket_thuc": ket_thuc,
            "ca_ngay": ca_ngay,
            "dia_diem": lam_sach(truong.get("LOCATION", ""), TRAN_DIA_DIEM),
            "mo_ta": lam_sach(truong.get("DESCRIPTION", ""), TRAN_MO_TA),
            "rrule_dict": _phan_tich_rrule(rrule_tho) if rrule_tho else None,
            "exdate": exdate,
        })
    su_kien.sort(key=lambda e: e["bat_dau"])
    return su_kien


# ── Mở rộng RRULE (có trần) ────────────────────────────────────────────

_TUAN = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _phan_tich_rrule(chuoi: str):
    """RRULE → dict hoặc None nếu hỏng/không hỗ trợ mở rộng.

    Chỉ mở rộng FREQ=DAILY/WEEKLY/MONTHLY/YEARLY; SECONDLY/MINUTELY/HOURLY
    («lặp mỗi giây») → None (coi như sự kiện đơn)."""
    try:
        phan: dict = {}
        for muc in chuoi.split(";"):
            if "=" not in muc:
                return None
            k, v = muc.split("=", 1)
            phan[k.strip().upper()] = v.strip().upper()
        freq = phan.get("FREQ", "")
        if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
            return None
        kq: dict = {"freq": freq, "interval": 1}
        if "INTERVAL" in phan:
            kq["interval"] = int(phan["INTERVAL"])
            if kq["interval"] < 1:
                return None
        if "COUNT" in phan:
            kq["count"] = int(phan["COUNT"])
            if kq["count"] < 1:
                return None
        if "UNTIL" in phan:
            kq["until_tho"] = phan["UNTIL"]
        if "BYDAY" in phan:
            ngay = []
            for d in phan["BYDAY"].split(","):
                d = d.strip()
                if d not in _TUAN:  # gồm cả dạng có tiền tố số thứ tự «2SU»
                    return None
                ngay.append(d)
            kq["byday"] = sorted(ngay, key=_TUAN.get)
        if "BYMONTHDAY" in phan:
            kq["bymonthday"] = [int(x) for x in phan["BYMONTHDAY"].split(",")]
            if not all(1 <= x <= 31 for x in kq["bymonthday"]):
                return None
        return kq
    except (ValueError, AttributeError):
        return None


def _ngay_cua_thang(nam: int, thang: int, ngay: int, gio: tuple, tz):
    """datetime(nam, thang, ngay, *gio) — ngày không tồn tại (30/2) → None."""
    try:
        return datetime(nam, thang, ngay, *gio, tzinfo=tz)
    except ValueError:
        return None


def _sinh_moc(su_kien: dict, rr: dict):
    """Sinh các mốc thô theo RRULE, tăng dần vô hạn (người gọi tự cắt).

    DTSTART là mốc đầu tiên; WEEKLY sinh theo thứ tự ngày trong tuần; MONTHLY bỏ
    qua ngày không tồn tại (30/2); YEARLY bỏ qua 29/2 vào năm không nhuận."""
    bat_dau: datetime = su_kien["bat_dau"]
    tz = bat_dau.tzinfo
    interval = rr["interval"]
    gio = (bat_dau.hour, bat_dau.minute, bat_dau.second)
    freq = rr["freq"]
    if freq == "DAILY":
        k = 0
        while True:
            yield bat_dau + timedelta(days=k * interval)
            k += 1
    elif freq == "WEEKLY":
        nguoc = {v: k for k, v in _TUAN.items()}
        byday = rr.get("byday") or [nguoc[bat_dau.weekday()]]
        thu_hai0 = bat_dau - timedelta(days=bat_dau.weekday())
        tuan = 0
        while True:
            for ten_ngay in byday:
                m = thu_hai0 + timedelta(weeks=tuan, days=_TUAN[ten_ngay])
                if m >= bat_dau:
                    yield m
            tuan += interval
    elif freq == "MONTHLY":
        ngay_ds = sorted(set(rr.get("bymonthday") or [bat_dau.day]))
        thang_off = 0
        while True:
            tong = (bat_dau.month - 1) + thang_off
            nam, th = bat_dau.year + tong // 12, tong % 12 + 1
            for ng in ngay_ds:
                m = _ngay_cua_thang(nam, th, ng, gio, tz)
                if m is not None and m >= bat_dau:
                    yield m
            thang_off += interval
    else:  # YEARLY
        nam_off = 0
        while True:
            m = _ngay_cua_thang(bat_dau.year + nam_off, bat_dau.month,
                                bat_dau.day, gio, tz)
            if m is not None and m >= bat_dau:
                yield m
            nam_off += interval


def mo_rong(su_kien: dict, den: datetime) -> list:
    """Mở rộng một sự kiện thành các mốc bắt đầu ≤ `den`.

    - Không có RRULE / RRULE không mở rộng được → đúng 1 mốc (nếu ≤ den).
    - Có RRULE → tôn trọng COUNT/UNTIL/INTERVAL/BYDAY/BYMONTHDAY/EXDATE;
      cắt ở TRAN_LAP_LAI mốc (chống nổ lặp vô hạn) và ở `den`.
      COUNT tính trên mốc THÔ (trước khi trừ EXDATE), đúng RFC 5545."""
    bat_dau: datetime = su_kien["bat_dau"]
    loai_tru: set = su_kien.get("exdate") or set()
    rr = su_kien.get("rrule_dict")
    if not rr:
        return [bat_dau] if bat_dau <= den and bat_dau not in loai_tru else []
    until = None
    if rr.get("until_tho"):
        until, _ = phan_tich_dt("DTSTART:" + rr["until_tho"], bat_dau.tzinfo)
    tran_dem = min(rr.get("count") or TRAN_LAP_LAI, TRAN_LAP_LAI)
    kq: list = []
    da_dem = 0
    for m in _sinh_moc(su_kien, rr):
        if m > den:
            break
        if until is not None and m > until:
            break
        da_dem += 1
        if da_dem > tran_dem:
            break
        if m in loai_tru:
            continue
        kq.append(m)
        if len(kq) >= TRAN_LAP_LAI:
            break
    kq.sort()
    return kq


# ── Tải ICS (qua đúng bộ kiểm app/tu_xa, không viết bộ kiểm thứ hai) ─────

async def tai_ics(url: str, *, het_gio: float = HET_GIO_TAI) -> str:
    """Tải nội dung ICS. Mỗi bước chuyển hướng được kiểm lại bằng `kiem_url_lan`
    của lõi (chống SSRF/DNS rebinding). Lỗi → LoiLich mang mã ngắn, không chứa URL."""
    import httpx
    from app.tu_xa import kiem_url_lan

    hien_tai = url
    async with httpx.AsyncClient(timeout=het_gio, follow_redirects=False) as khach:
        for _ in range(TRAN_CHUYEN_HUONG + 1):
            try:
                hien_tai = await kiem_url_lan(hien_tai)
            except ValueError as e:
                raise LoiLich("chuyen_huong_bi_chan", str(e)) from None
            try:
                async with khach.stream("GET", hien_tai,
                                        headers={"User-Agent": UA}) as resp:
                    ma = resp.status_code
                    if ma in (301, 302, 303, 307, 308):
                        dich = resp.headers.get("location", "")
                        if not dich:
                            raise LoiLich("mang_loi", "chuyển hướng không có Location")
                        hien_tai = urljoin(hien_tai, dich)
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
