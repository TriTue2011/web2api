"""Vòng SỬA NV159 (lich-ics) — test cho các phát hiện C1–C5 của soát độc lập B mục 4.

C1 (CAO): _sinh_moc sinh vô hạn / OverflowError với RRULE ác ý → treo tiến trình.
C2 (TB): URL lịch bí mật lọt log vì bộ lọc dò chuỗi con URL gốc.
C3 (TB): mở rộng lặp sai/thiếu — đếm từ DTSTART, RECURRENCE-ID, BYMONTH/BYDAY thứ tự/
        UNTIL chỉ ngày, thành phần lạ bị bỏ qua lặng lẽ.
C4 (THẤP): docstring hứa chống DNS rebinding nhưng tai_ics không ghim IP.
C5 (THẤP): không có trần chi phí mở rộng chung cho một lần gọi.

Mọi ca chạy qua helper có TRẦN THỜI GIAN: mã lỗi (treo) phải ĐỎ bằng timeout
chứ không treo cả phiên test.
"""

from __future__ import annotations

import asyncio
import http.server
import importlib.util
import json
import logging
import socket
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.kham_pha import doc_manifest  # noqa: E402

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "lich-ics"

TZ_HN = ZoneInfo("Asia/Ho_Chi_Minh")

GOI = "modules_lich_ics_sua_pkg"


def _tai_module(ten_goi: str = GOI):
    tep = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location(
        ten_goi, tep, submodule_search_locations=[str(THU_MUC)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten_goi] = mod
    spec.loader.exec_module(mod)
    return mod


def _tai_lich(ten_goi: str = GOI + "_lich"):
    tep = THU_MUC / "lich.py"
    spec = importlib.util.spec_from_file_location(ten_goi, tep)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten_goi] = mod
    spec.loader.exec_module(mod)
    return mod


def _manifest():
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    return m


def _chay_co_tran(ham, giay: float = 15.0):
    """Chạy `ham` trong luồng daemon với trần thời gian — treo = test ĐỎ,
    không treo cả phiên pytest."""
    kq: dict = {}

    def chay():
        try:
            kq["v"] = ham()
        except BaseException as e:  # noqa: BLE001
            kq["e"] = e

    t = threading.Thread(target=chay, daemon=True)
    t.start()
    t.join(giay)
    assert not t.is_alive(), f"quá {giay}s — nghi TREO tiến trình (C1)"
    if "e" in kq:
        raise kq["e"]
    return kq["v"]


class MayChuICSGia:
    """Máy chủ ICS giả trên 127.0.0.1 (sao khuôn test NV159 gốc)."""

    def __init__(self, noi_dung: str = "", chuyen_huong: str | None = None):
        self.noi_dung = noi_dung
        self.chuyen_huong = chuyen_huong
        self.so_luot_goi = 0
        self._srv = None
        self._luong = None
        self.url = ""
        self.cong = 0

    def __enter__(self):
        that = self

        class XuLy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                that.so_luot_goi += 1
                if that.chuyen_huong:
                    self.send_response(302)
                    self.send_header("Location", that.chuyen_huong)
                    self.end_headers()
                    return
                du_lieu = that.noi_dung.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/calendar")
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)

            def log_message(self, *a):
                pass

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.cong = s.getsockname()[1]
        self._srv = http.server.HTTPServer(("127.0.0.1", self.cong), XuLy)
        self._luong = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._luong.start()
        self.url = f"http://127.0.0.1:{self.cong}/lich.ics"
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._luong.join(5)


ICS_RONG = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"


class MayChuDichV6:
    """Máy chủ đích nghe trên ::1 — vì «localhost» phân giải ra ::1 TRƯỚC
    127.0.0.1 trên máy này và TransportGhimIp của lõi ghim IP đầu tiên qua
    kiểm; đích redirect trong test C2 phải nghe được ở ::1."""

    def __init__(self, noi_dung: str):
        self.noi_dung = noi_dung
        self.cong = 0

    def __enter__(self):
        that = self

        class XuLy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                du_lieu = that.noi_dung.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/calendar")
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)

            def log_message(self, *a):
                pass

        class MayChu6(http.server.HTTPServer):
            address_family = socket.AF_INET6

        with socket.socket(socket.AF_INET6) as s:
            s.bind(("::1", 0))
            self.cong = s.getsockname()[1]
        self._srv = MayChu6(("::1", self.cong), XuLy)
        self._luong = threading.Thread(target=self._srv.serve_forever,
                                        daemon=True)
        self._luong.start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._luong.join(5)


@pytest.fixture
def loopback_cho_phep(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


def _ics_rrule(rrule: str, dtstart: str = "DTSTART:20260202T090000",
               them: str = "") -> str:
    return ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:r1\nDTSTAMP:20260101T000000Z\n"
            f"{dtstart}\nRRULE:{rrule}\n{them}SUMMARY:Lặp\nEND:VEVENT\nEND:VCALENDAR")


def _mot_sk(lich, ics: str) -> dict:
    sk = lich.phan_tich_ics(ics, TZ_HN)
    assert len(sk) >= 1
    return sk[0]


# ── C1: RRULE ác ý không được treo / không được ném lỗi số học ───────────

def test_c1_monthly_31_thang2_khong_treo():
    """FREQ=MONTHLY;BYMONTHDAY=31;INTERVAL=12 từ tháng 2: không tháng nào có
    ngày 31 → bản cũ sinh vô hạn, treo cả tiến trình."""
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=MONTHLY;BYMONTHDAY=31;INTERVAL=12",
                                  dtstart="DTSTART:20260215T090000"))
    den = datetime(2030, 1, 1, tzinfo=TZ_HN)
    moc = _chay_co_tran(lambda: lich.mo_rong(sk, den), giay=10)
    assert moc == []


def test_c1_interval_khong_lo_khong_loi_so_hoc():
    """INTERVAL cực lớn: bản cũ OverflowError (DAILY/WEEKLY) hoặc treo (YEARLY)."""
    lich = _tai_lich()
    den = datetime(2027, 1, 1, tzinfo=TZ_HN)
    for rrule in ("FREQ=DAILY;INTERVAL=99999999",
                  "FREQ=YEARLY;INTERVAL=9000",
                  "FREQ=WEEKLY;INTERVAL=99999999999"):
        sk = _mot_sk(lich, _ics_rrule(rrule))
        moc = _chay_co_tran(lambda sk=sk: lich.mo_rong(sk, den), giay=10)
        assert len(moc) == 1, rrule  # chỉ chính DTSTART, không lỗi, không treo


def test_c1_count_khong_lo_bi_tran():
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=DAILY;COUNT=1000000000"))
    den = datetime(2028, 1, 1, tzinfo=TZ_HN)
    moc = _chay_co_tran(lambda: lich.mo_rong(sk, den), giay=10)
    assert len(moc) == lich.TRAN_LAP_LAI


def test_c1_exdate_khong_lo():
    """EXDATE 100 buổi đầu của RRULE COUNT=4000: 100 buổi đầu bị loại nhưng
    các buổi sau vẫn hiện tới trần 500 mốc TRẢ VỀ (vòng sửa đổi có chủ ý:
    bản cũ tính trần trên mốc THÔ nên mất luôn 100 buổi thật; chi phí sinh
    vẫn bị chặn bởi TRAN_VONG_SINH + ngân sách gom_moc tính theo mốc sinh)."""
    lich = _tai_lich()
    goc = datetime(2026, 2, 2, 9, 0, tzinfo=TZ_HN)
    ex = ",".join((goc + timedelta(days=i)).strftime("%Y%m%dT%H%M%S")
                  for i in range(100))
    ics = _ics_rrule("FREQ=DAILY;COUNT=4000", them=f"EXDATE:{ex}\n")
    sk = _mot_sk(lich, ics)
    den = datetime(2027, 12, 31, tzinfo=TZ_HN)
    moc = _chay_co_tran(lambda: lich.mo_rong(sk, den), giay=10)
    assert len(moc) == lich.TRAN_LAP_LAI
    assert moc[0] == goc + timedelta(days=100)   # buổi thứ 101
    assert moc[-1] == goc + timedelta(days=599)
    assert all(m >= goc + timedelta(days=100) for m in moc)


def test_c1_su_kien_hong_khong_hong_ca_lich():
    """gom_moc: một sự kiện hỏng cấu trúc bị BỎ + ĐẾM, sự kiện lành vẫn mở rộng."""
    lich = _tai_lich()
    goc = datetime(2026, 2, 2, 9, 0, tzinfo=TZ_HN)
    sk_lanh = _mot_sk(lich, _ics_rrule("FREQ=DAILY;COUNT=3"))
    sk_hong = {"uid": "hong", "tieu_de": "Hỏng", "ket_thuc": None,
               "ca_ngay": False, "dia_diem": "", "mo_ta": "",
               "rrule_dict": {"freq": "DAILY", "interval": 1},
               "exdate": set(), "lap_phuc_tap": False}  # thiếu «bat_dau»
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    bang, thong_ke = _chay_co_tran(
        lambda: lich.gom_moc([sk_hong, sk_lanh], den), giay=10)
    assert thong_ke["bi_loi"] == 1
    assert bang[0] == []
    assert len(bang[1]) == 3


def test_c1_ics_ac_y_qua_thuc_hien_khong_treo(loopback_cho_phep):
    """Cả tệp chứa 3 dạng RRULE ác ý + 1 sự kiện lành: công cụ vẫn trả sự
    kiện lành trong thời gian hữu hạn (bản cũ treo ở sự kiện đầu tiên)."""
    nay = datetime.now(TZ_HN)
    nam = nay.year
    lanh = (nay + timedelta(days=2)).replace(hour=9, minute=0, second=0,
                                             microsecond=0)
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\n"
        "BEGIN:VEVENT\nUID:ac1\nDTSTART:" + f"{nam}0215T090000" + "\n"
        "RRULE:FREQ=MONTHLY;BYMONTHDAY=31;INTERVAL=12\nSUMMARY:Ác 1\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:ac2\nDTSTART:20260202T090000\n"
        "RRULE:FREQ=YEARLY;INTERVAL=9000\nSUMMARY:Ác 2\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:ac3\nDTSTART:20260202T090000\n"
        "RRULE:FREQ=DAILY;INTERVAL=99999999\nSUMMARY:Ác 3\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:lanh\n"
        f"DTSTART:{lanh.strftime('%Y%m%dT%H%M%S')}\nSUMMARY:Sự kiện lành\nEND:VEVENT\n"
        "END:VCALENDAR\n")
    with MayChuICSGia(ics) as srv:
        mod = _tai_module(GOI + "_c1")
        inst = mod.Module(_manifest())
        inst.nap({"lich": [{"id": "l1", "ten": "L", "url": srv.url, "bat": True}],
                  "so_ngay_mac_dinh": 7, "toi_da_mac_dinh": 30,
                  "cache_giay": 900, "mui_gio": "Asia/Ho_Chi_Minh"})
        kq = _chay_co_tran(
            lambda: asyncio.run(
                inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi",
                               {"so_ngay": 7, "toi_da": 30})),
            giay=20)
    assert kq["ok"] is True
    tieu_de = [s["tieu_de"] for s in kq["ket_qua"]["su_kien"]]
    assert "Sự kiện lành" in tieu_de


# ── C3a: nhảy thẳng tới cửa sổ (không đếm từ DTSTART) ───────────────────

def _moc_daily_doc_lap(dtstart: datetime, count: int | None,
                       tu: datetime, den: datetime) -> list:
    """Sinh mốc DAILY độc lập trong test (không dùng mã module) để đối chiếu."""
    kq = []
    k = 0
    while count is None or k < count:
        m = dtstart + timedelta(days=k)
        if m > den:
            break
        if m >= tu:
            kq.append(m)
        k += 1
    return kq


def test_c3a_daily_cu_600_ngay_van_thay_trong_cua_so():
    """Bản cũ đếm 500 mốc từ DTSTART → sự kiện lặp hằng ngày bắt đầu 600 ngày
    trước BIẾN MẤT khỏi cửa sổ 7 ngày tới."""
    lich = _tai_lich()
    nay = datetime.now(TZ_HN)
    bd = (nay - timedelta(days=600)).replace(hour=9, minute=0, second=0,
                                              microsecond=0)
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:cu\n"
           f"DTSTART:{bd.strftime('%Y%m%dT%H%M%S')}\n"
           "RRULE:FREQ=DAILY\nSUMMARY:Cũ\nEND:VEVENT\nEND:VCALENDAR")
    sk = _mot_sk(lich, ics)
    tu = nay - timedelta(hours=2)
    den = nay + timedelta(days=7)
    moc = [m for m in lich.mo_rong(sk, den, tu=tu) if m >= tu]
    assert moc == _moc_daily_doc_lap(bd, None, tu, den)
    assert len(moc) >= 7


def test_c3a_weekly_cu_10_nam_van_thay():
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=WEEKLY",
                                  dtstart="DTSTART:20160201T090000"))  # thứ Hai
    tu = datetime(2026, 10, 19, 0, 0, tzinfo=TZ_HN)   # thứ Hai
    den = datetime(2026, 10, 26, 0, 0, tzinfo=TZ_HN)
    moc = [m for m in lich.mo_rong(sk, den, tu=tu) if m >= tu]
    assert moc == [datetime(2026, 10, 19, 9, 0, tzinfo=TZ_HN)]


def test_c3a_count_da_het_khi_nhay():
    """COUNT=10 mà DTSTART cách 600 ngày → đã hết từ lâu, cửa sổ phải rỗng;
    COUNT=700 → chỉ các mốc chỉ số 600..699 rơi vào cửa sổ mới hiện."""
    lich = _tai_lich()
    nay = datetime.now(TZ_HN)
    bd = (nay - timedelta(days=600)).replace(hour=9, minute=0, second=0,
                                              microsecond=0)
    tu = nay - timedelta(hours=2)
    den = nay + timedelta(days=7)
    for count, mong_doi in ((10, 0), (700, None)):
        ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:cu\n"
               f"DTSTART:{bd.strftime('%Y%m%dT%H%M%S')}\n"
               f"RRULE:FREQ=DAILY;COUNT={count}\nSUMMARY:Cũ\nEND:VEVENT\nEND:VCALENDAR")
        sk = _mot_sk(lich, ics)
        moc = [m for m in lich.mo_rong(sk, den, tu=tu) if m >= tu]
        doc_lap = _moc_daily_doc_lap(bd, count, tu, den)
        assert moc == doc_lap, count
        if mong_doi is not None:
            assert len(moc) == mong_doi


def test_c3a_doi_chieu_nhay_voi_khong_nhay():
    """Bảng ca RRULE đa dạng: mở rộng có nhảy (tu) rồi lọc ≥ tu phải BẰNG
    mở rộng từ DTSTART rồi lọc ≥ tu (mọi ca đều dưới trần 500 mốc)."""
    lich = _tai_lich()
    tu = datetime(2025, 6, 15, 0, 0, tzinfo=TZ_HN)
    den = datetime(2026, 3, 1, 0, 0, tzinfo=TZ_HN)
    ca = [
        ("FREQ=DAILY;INTERVAL=3;COUNT=400", "DTSTART:20240102T090000"),
        ("FREQ=WEEKLY;BYDAY=MO,FR;COUNT=300", "DTSTART:20240102T090000"),
        ("FREQ=WEEKLY;BYDAY=WE;INTERVAL=2;COUNT=200", "DTSTART:20240102T090000"),
        ("FREQ=MONTHLY;BYMONTHDAY=1,15;COUNT=40", "DTSTART:20240102T090000"),
        ("FREQ=MONTHLY;BYDAY=2TU;COUNT=30", "DTSTART:20240102T090000"),
        ("FREQ=MONTHLY;BYMONTHDAY=31;COUNT=36", "DTSTART:20240102T090000"),
        ("FREQ=YEARLY;COUNT=8", "DTSTART:20240102T090000"),
        ("FREQ=DAILY;BYMONTH=1,7;COUNT=200", "DTSTART:20240102T090000"),
        ("FREQ=YEARLY;BYMONTH=3,6;BYMONTHDAY=15;COUNT=10",
         "DTSTART:20240110T090000"),
    ]
    for rrule, dtstart in ca:
        sk = _mot_sk(lich, _ics_rrule(rrule, dtstart=dtstart))
        co_nhay = [m for m in lich.mo_rong(sk, den, tu=tu) if m >= tu]
        khong_nhay = [m for m in lich.mo_rong(sk, den) if m >= tu]
        assert co_nhay == khong_nhay, rrule


# ── C3b: RECURRENCE-ID ──────────────────────────────────────────────────

def test_c3b_recurrence_id_bo_buoi_goc():
    """Buổi bị dời giờ: bản cũ hiện CẢ gốc 09:00 lẫn bản dời 15:00 cùng ngày.
    Đúng: buổi gốc bị RECURRENCE-ID loại, chỉ còn buổi dời."""
    lich = _tai_lich()
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\n"
        "BEGIN:VEVENT\nUID:day-lam@v\nDTSTART:20260202T090000\n"
        "DTEND:20260202T100000\nRRULE:FREQ=DAILY;COUNT=5\n"
        "SUMMARY:Họp đứng\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:day-lam@v\nRECURRENCE-ID:20260204T090000\n"
        "DTSTART:20260204T150000\nDTEND:20260204T160000\n"
        "SUMMARY:Họp đứng (dời)\nEND:VEVENT\n"
        "END:VCALENDAR\n")
    sk = lich.phan_tich_ics(ics, TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    tat_ca = []
    for e in sk:
        for m in lich.mo_rong(e, den):
            tat_ca.append((m, e["tieu_de"]))
    tat_ca.sort()
    ngay4 = [t for t in tat_ca if t[0].day == 4]
    assert ngay4 == [(datetime(2026, 2, 4, 15, 0, tzinfo=TZ_HN), "Họp đứng (dời)")]
    assert len(tat_ca) == 5  # 4 buổi gốc còn lại + 1 buổi dời


# ── C3c: thành phần RRULE từng bị bỏ qua lặng lẽ / hiểu sai ─────────────

def test_c3c_yearly_bymonth_dung_thang():
    """Bản cũ bỏ qua BYMONTH: YEARLY;BYMONTH=3 cho 15/01 mỗi năm (sai tháng)."""
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=YEARLY;BYMONTH=3",
                                  dtstart="DTSTART:20250115T090000"))
    den = datetime(2027, 1, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert moc == [datetime(2025, 3, 15, 9, 0, tzinfo=TZ_HN),
                   datetime(2026, 3, 15, 9, 0, tzinfo=TZ_HN)]


def test_c3c_yearly_bymonth_bymonthday():
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=YEARLY;BYMONTH=3,6;BYMONTHDAY=15;COUNT=4",
                                  dtstart="DTSTART:20250110T090000"))
    den = datetime(2028, 1, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert [(m.month, m.day) for m in moc] == [(3, 15), (6, 15), (3, 15), (6, 15)]
    assert [m.year for m in moc] == [2025, 2025, 2026, 2026]


def test_c3c_monthly_byday_thu_tu():
    """«Thứ Hai đầu tiên của tháng» — bản cũ trả rrule None (chỉ còn 1 mốc)."""
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=MONTHLY;BYDAY=1MO;COUNT=3",
                                  dtstart="DTSTART:20260105T090000"))
    den = datetime(2026, 12, 31, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert [(m.month, m.day) for m in moc] == [(1, 5), (2, 2), (3, 2)]
    assert all(m.weekday() == 0 for m in moc)


def test_c3c_monthly_byday_cuoi_thang():
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=MONTHLY;BYDAY=-1FR;COUNT=3",
                                  dtstart="DTSTART:20260130T090000"))
    den = datetime(2026, 12, 31, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert [(m.month, m.day) for m in moc] == [(1, 30), (2, 27), (3, 27)]
    assert all(m.weekday() == 4 for m in moc)


def test_c3c_bymonthday_am_la_cuoi_thang():
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3",
                                  dtstart="DTSTART:20260131T090000"))
    den = datetime(2026, 12, 31, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert [(m.month, m.day) for m in moc] == [(1, 31), (2, 28), (3, 31)]


def test_c3c_bysetpos_ngay_lam_viec_cuoi_thang():
    lich = _tai_lich()
    sk = _mot_sk(
        lich, _ics_rrule("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3",
                         dtstart="DTSTART:20260130T090000"))
    den = datetime(2026, 12, 31, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert [(m.month, m.day) for m in moc] == [(1, 30), (2, 27), (3, 31)]


def test_c3c_until_chi_ngay_giu_buoi_cuoi_ngay():
    """UNTIL=20260204 (chỉ ngày) nghĩa là hết NGÀY 04/02: buổi 09:00 ngày 04
    vẫn thuộc tập lặp. Bản cũ parse thành 00:00 → mất buổi cuối."""
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=DAILY;UNTIL=20260204"))
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk, den)
    assert [m.day for m in moc] == [2, 3, 4]


def test_c3c_rrule_phuc_tap_duoc_co_va_ghi_chu(loopback_cho_phep):
    """Thành phần KHÔNG hỗ trợ (BYHOUR): bản cũ bỏ qua lặng lẽ rồi mở rộng
    như thường. Nay: sự kiện mang cờ lap_phuc_tap, chỉ hiện lần đầu, và kết
    quả công cụ có ghi_chu nói rõ."""
    lich = _tai_lich()
    sk = _mot_sk(lich, _ics_rrule("FREQ=DAILY;BYHOUR=9;COUNT=5"))
    assert sk["lap_phuc_tap"] is True
    assert sk["rrule_dict"] is None
    sk_thuong = _mot_sk(lich, _ics_rrule("FREQ=DAILY;COUNT=5"))
    assert sk_thuong["lap_phuc_tap"] is False

    nay = datetime.now(TZ_HN)
    bd = (nay + timedelta(days=1)).replace(hour=9, minute=0, second=0,
                                           microsecond=0)
    ics = ("BEGIN:VCALENDAR\nVERSION:2.0\n"
           "BEGIN:VEVENT\nUID:pt\n"
           f"DTSTART:{bd.strftime('%Y%m%dT%H%M%S')}\n"
           "RRULE:FREQ=DAILY;BYHOUR=9;COUNT=5\nSUMMARY:Phức tạp\nEND:VEVENT\n"
           "END:VCALENDAR\n")
    with MayChuICSGia(ics) as srv:
        mod = _tai_module(GOI + "_c3c")
        inst = mod.Module(_manifest())
        inst.nap({"lich": [{"id": "l1", "ten": "L", "url": srv.url, "bat": True}],
                  "so_ngay_mac_dinh": 7, "toi_da_mac_dinh": 30,
                  "cache_giay": 900, "mui_gio": "Asia/Ho_Chi_Minh"})
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi",
                                        {"so_ngay": 7, "toi_da": 30}))
    assert kq["ok"] is True
    assert "phức tạp" in kq["ket_qua"].get("ghi_chu", "")


# ── C5: trần tổng số mốc cho MỘT lần mở rộng + nhảy cửa sổ tiết kiệm ────

def test_c5_tran_tong_moc_mot_lan_goi():
    """1000 sự kiện × 32 mốc tiềm năng = 32000 > trần tổng → bị cắt ở trần
    và đánh dấu da_cat (bản cũ không có khái niệm này)."""
    lich = _tai_lich()
    nay = datetime.now(TZ_HN)
    bd = nay.replace(hour=9, minute=0, second=0, microsecond=0)
    mot = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\n"
           f"DTSTART:{bd.strftime('%Y%m%dT%H%M%S')}\n"
           "RRULE:FREQ=DAILY\nSUMMARY:X\nEND:VEVENT\nEND:VCALENDAR")
    sk = _mot_sk(lich, mot)
    ds = [dict(sk, uid=f"u{i}") for i in range(1000)]
    den = nay + timedelta(days=31)
    bang, thong_ke = lich.gom_moc(ds, den)
    assert thong_ke["tong_moc"] <= lich.TRAN_TONG_MOC
    assert thong_ke["da_cat"] is True
    assert len(bang[0]) == 32  # sự kiện đầu được mở rộng đầy đủ


def test_c5_nhay_cua_so_tiet_kiem_ngan_sach():
    """1500 sự kiện DAILY;COUNT=800 bắt đầu 500 ngày trước: KHÔNG nhảy thì
    ~508 mốc/sự kiện phải sinh mới tới cửa sổ (762.000 mốc — trần tổng cắt
    ngay); có nhảy thì chỉ ~9 mốc/sự kiện quanh cửa sổ 7 ngày — vừa ngân
    sách, không bị cắt, COUNT=800 vẫn còn hiệu lực trong cửa sổ."""
    lich = _tai_lich()
    nay = datetime.now(TZ_HN)
    bd = (nay - timedelta(days=500)).replace(hour=9, minute=0, second=0,
                                             microsecond=0)
    mot = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\n"
           f"DTSTART:{bd.strftime('%Y%m%dT%H%M%S')}\n"
           "RRULE:FREQ=DAILY;COUNT=800\nSUMMARY:X\nEND:VEVENT\nEND:VCALENDAR")
    sk = _mot_sk(lich, mot)
    ds = [dict(sk, uid=f"u{i}") for i in range(1500)]
    tu = nay - timedelta(hours=2)
    den = nay + timedelta(days=7)
    bang, thong_ke = _chay_co_tran(lambda: lich.gom_moc(ds, den, tu=tu), giay=20)
    assert thong_ke["da_cat"] is False
    assert thong_ke["tong_moc"] < lich.TRAN_TONG_MOC
    # mốc trong cửa sổ + tối đa 2 mốc của kỳ lùi an toàn
    assert all(8 <= len(b) <= 10 for b in bang[:5])


# ── C2: bộ lọc log che theo CẤU TRÚC (tên máy chuẩn hoá) ─────────────────

def _bo_loc_cua(ten_goi: str):
    for f in logging.getLogger("httpx").filters:
        if getattr(f, "_lich_ics_loc", False) and type(f).__module__ == ten_goi:
            return f
    raise AssertionError(f"không thấy bộ lọc của {ten_goi}")


def _ban_ghi(thong_diep: str) -> logging.LogRecord:
    return logging.LogRecord("httpx", logging.WARNING, __file__, 1,
                             thong_diep, (), None)


def test_c2_loc_theo_ten_may_chuan_hoa():
    """URL gốc lưu dạng «http://LICH.EXAMPLE.com:8080/…/BI-MAT.ics/» (host
    HOA + «/» cuối); dòng log httpx chứa URL đã chuẩn hoá (host thường, mất
    «/» cuối do kiem_url_lan rstrip) — bản cũ dò chuỗi con nên LỌT."""
    ten = GOI + "_c2a"
    mod = _tai_module(ten)
    inst = mod.Module(_manifest())
    inst.nap({"lich": [{"id": "l1", "ten": "L", "bat": True,
                        "url": "http://LICH.EXAMPLE.com:8080/duong/BI-MAT.ics/"}],
              "mui_gio": "Asia/Ho_Chi_Minh"})
    loc = _bo_loc_cua(ten)
    # các biến thể của CÙNG một URL bí mật đều phải bị chặn
    assert loc.filter(_ban_ghi(
        'HTTP Request: GET http://lich.example.com:8080/duong/BI-MAT.ics '
        '"HTTP/1.1 200 OK"')) is False
    assert loc.filter(_ban_ghi(
        "GET http://LICH.EXAMPLE.COM:8080/duong/BI-MAT.ics/")) is False
    assert loc.filter(_ban_ghi(
        "GET http://lich.example.com:8080/duong/BI-MAT.ics")) is False
    # URL của máy khác không liên quan → cho qua
    assert loc.filter(_ban_ghi(
        "GET http://may-khac.example.com:8080/duong/BI-MAT.ics")) is True
    assert loc.filter(_ban_ghi("dòng log bình thường")) is True


def test_c2_host_dich_chuyen_huong_cung_bi_chan(loopback_cho_phep):
    """Đích chuyển hướng (host khác hẳn URL gốc) không nằm trong tập URL đã
    đăng ký ở bản cũ → log của nó lọt. Nay tai_ics ghi lại host đã tải và
    bộ lọc chặn theo host đó."""
    ten = GOI + "_c2b"
    with MayChuDichV6(ICS_RONG.replace("END:VCALENDAR",
                                       "BEGIN:VEVENT\nUID:u\n"
                                       "DTSTART:20260215T090000\nSUMMARY:X\n"
                                       "END:VEVENT\nEND:VCALENDAR")) as dich:
        url_dich = f"http://LOCALHOST:{dich.cong}/lich.ics"
        with MayChuICSGia(chuyen_huong=url_dich) as goc:
            mod = _tai_module(ten)
            inst = mod.Module(_manifest())
            r = asyncio.run(inst.cai({
                "ten_lich": "L", "url_lich": [goc.url],
                "so_ngay_mac_dinh": "7", "toi_da_mac_dinh": "30",
                "cache_giay": "900", "mui_gio": "Asia/Ho_Chi_Minh"}))
            assert r["ok"] is True, r
            lich_mod = mod.lich_mod
            assert "localhost" in lich_mod.hosts_da_tai()
            loc = _bo_loc_cua(ten)
            assert loc.filter(_ban_ghi(
                f'HTTP Request: GET http://localhost:{dich.cong}/lich.ics '
                '"HTTP/1.1 200 OK"')) is False
            # host gốc (127.0.0.1) cũng bị chặn như trước
            assert loc.filter(_ban_ghi(
                f"GET http://127.0.0.1:{goc.cong}/lich.ics")) is False


# ── C4: tai_ics phải GHIM IP lúc nối (TransportGhimIp của lõi) ──────────

def test_c4_tai_ics_dung_transport_ghim_ip(loopback_cho_phep, monkeypatch):
    """Docstring hứa «chống DNS rebinding» — nay phải thật: client tải ICS
    dùng TransportGhimIp (phân giải + kiểm IP ngay lúc nối)."""
    lich = _tai_lich(GOI + "_c4")
    import httpx as httpx_that
    from app.chuyen_tiep import TransportGhimIp

    ghi: dict = {}
    LopThat = httpx_that.AsyncClient

    class LopGhi(LopThat):
        def __init__(self, *a, **kw):
            ghi["transport"] = kw.get("transport")
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx_that, "AsyncClient", LopGhi)
    with MayChuICSGia(ICS_RONG) as srv:
        noi_dung = asyncio.run(lich.tai_ics(srv.url))
    assert "BEGIN:VCALENDAR" in noi_dung
    assert isinstance(ghi.get("transport"), TransportGhimIp)


def test_c4_ghim_ip_chan_khi_doi_chieu_vong_kiem(monkeypatch):
    """Không có cờ test loopback: IP chữ 127.0.0.1 bị chặn NGAY ở tầng vận
    chuyển (và ở kiem_url_lan) — tai_ics trả LoiLich, không rò nội dung."""
    lich = _tai_lich(GOI + "_c4b")
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    with MayChuICSGia(ICS_RONG) as srv:
        with pytest.raises(lich.LoiLich):
            asyncio.run(lich.tai_ics(srv.url))
