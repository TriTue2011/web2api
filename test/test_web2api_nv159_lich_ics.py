"""Module lich-ics (NV159): đọc lịch ICS, CHỈ ĐỌC, trả sự kiện sắp tới (chuẩn C2 công cụ).

Test-first: mọi hành vi bảo mật (trần tệp, trần RRULE, TZID, SSRF, bí mật không vào log)
được viết TRƯỚC, phải ĐỎ khi mã chưa có / bị đột biến, XANH khi đúng.
Dịch vụ GIẢ chạy cục bộ 127.0.0.1 (conftest chặn Internet thật).
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

from app.kham_pha import doc_manifest, kiem_manifest  # noqa: E402

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "lich-ics"

TZ_HN = ZoneInfo("Asia/Ho_Chi_Minh")
TZ_NY = ZoneInfo("America/New_York")


def _tai_module(ten_goi: str = "modules_lich_ics_pkg"):
    tep = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location(
        ten_goi, tep, submodule_search_locations=[str(THU_MUC)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten_goi] = mod
    spec.loader.exec_module(mod)
    return mod


def _tai_lich(ten_goi: str = "modules_lich_ics_lich_pkg"):
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


def _module(mod=None):
    mod = mod or _tai_module()
    m = _manifest()
    return mod.Module(m)


ICS_CO_BAN = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//VI
BEGIN:VEVENT
UID:evt-1@test
DTSTAMP:20260101T000000Z
DTSTART:20260215T090000
DTEND:20260215T100000
SUMMARY:Họp nhóm
LOCATION:Phòng A
DESCRIPTION:Bàn kế hoạch quý
END:VEVENT
BEGIN:VEVENT
UID:evt-2@test
DTSTAMP:20260101T000000Z
DTSTART:20260216T140000Z
DTEND:20260216T150000Z
SUMMARY:Gọi khách hàng
END:VEVENT
BEGIN:VEVENT
UID:evt-3@test
DTSTAMP:20260101T000000Z
DTSTART;VALUE=DATE:20260220
SUMMARY:Nghỉ lễ
END:VEVENT
END:VCALENDAR
"""


def _ics_dong() -> str:
    """ICS sinh động theo NGÀY HIỆN TẠI (không phụ thuộc ngày chạy test):
    3 sự kiện ở +2, +3, +5 ngày — nằm trong cửa sổ mặc định 7 ngày."""
    hn = ZoneInfo("Asia/Ho_Chi_Minh")
    nay = datetime.now(hn)

    def fmt(dt):
        return dt.strftime("%Y%m%dT%H%M%S")

    e1 = (nay + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    e2 = (nay + timedelta(days=3)).replace(
        hour=14, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    e3 = (nay + timedelta(days=5)).date()
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//vi\r\n"
        "BEGIN:VEVENT\r\nUID:evt-dong-1\r\n"
        f"DTSTART:{fmt(e1)}\r\nDTEND:{fmt(e1 + timedelta(hours=1))}\r\n"
        "SUMMARY:Họp nhóm\r\nLOCATION:Phòng A\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:evt-dong-2\r\n"
        f"DTSTART:{e2.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        "SUMMARY:Sự kiện UTC\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:evt-dong-3\r\n"
        f"DTSTART;VALUE=DATE:{e3.strftime('%Y%m%d')}\r\n"
        "SUMMARY:Cả ngày sự kiện\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


class MayChuICSGia:
    """Máy chủ ICS giả: phục vụ nội dung tuỳ biến, đếm số lượt gọi."""

    def __init__(self, noi_dung: str = ICS_CO_BAN, ma: int = 200,
                 tieu_de: dict | None = None, chuyen_huong: str | None = None):
        self.noi_dung = noi_dung
        self.ma = ma
        self.tieu_de = tieu_de or {"Content-Type": "text/calendar"}
        self.chuyen_huong = chuyen_huong
        self.so_luot_goi = 0
        self._srv = None
        self._luong = None
        self.url = ""

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
                self.send_response(that.ma)
                for k, v in that.tieu_de.items():
                    self.send_header(k, v)
                du_lieu = that.noi_dung.encode("utf-8")
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)

            def log_message(self, *a):
                pass

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cong = s.getsockname()[1]
        self._srv = http.server.HTTPServer(("127.0.0.1", cong), XuLy)
        self._luong = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._luong.start()
        self.url = f"http://127.0.0.1:{cong}/lich.ics"
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._luong.join(5)


def _cau_hinh(url, **them):
    ch = {
        "ten_lich": "Lịch nhà",
        "url_lich": [url],
        "so_ngay_mac_dinh": "7",
        "toi_da_mac_dinh": "30",
        "cache_giay": "900",
        "mui_gio": "Asia/Ho_Chi_Minh",
    }
    ch.update(them)
    return ch


@pytest.fixture
def loopback_cho_phep(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


# ── A. manifest & hợp đồng ──────────────────────────────────────────────

def test_manifest_hop_le():
    m = _manifest()
    assert kiem_manifest(m) is None, kiem_manifest(m)
    assert m["id"] == "lich-ics"
    assert m["tien_to"] == "lich_ics"
    assert m["cung_cap"] == ["thiet_bi"]
    assert m["hang"] in ("A", "B")
    khoa = {t["khoa"]: t for t in m["truong_cau_hinh"]}
    assert khoa["url_lich"]["kieu"] == "danh_sach_bi_mat"
    assert khoa["ten_lich"]["kieu"] == "chuoi"
    assert "lấy" in khoa["url_lich"]["mo_ta"].lower()  # hướng dẫn lấy link ở đâu
    assert m["tai_nguyen"]["dia_mb"] is None  # chưa đo — không bịa


def test_lop_du_phuong_thuc_hop_dong():
    mod = _tai_module()
    lop = mod.Module
    for pt in ("nap", "cai", "suc_khoe", "dung", "thuc_the", "trang_thai", "thuc_hien"):
        assert callable(getattr(lop, pt, None)), f"thiếu {pt}"


def test_thuc_the_khai_cong_cu_c2():
    mod = _tai_module()
    inst = _module(mod)
    inst.nap(_cau_hinh("http://example.invalid/x.ics"))
    ents = asyncio.run(inst.thuc_the())
    assert len(ents) == 1
    e = ents[0]
    assert e["id"] == "lich_ics.lich"
    assert e["loai"] == "cong_cu"
    assert e["rui_ro_mac_dinh"] == "doc"  # chỉ đọc
    ten_hd = {t["hanh_dong"] for t in e["tinh_nang"]}
    assert {"su_kien_sap_toi", "kiem_tra_lich"} <= ten_hd
    for t in e["tinh_nang"]:
        assert t["tham_so"]["additionalProperties"] is False


def test_trang_thai():
    mod = _tai_module()
    inst = _module(mod)
    r = asyncio.run(inst.trang_thai(["lich_ics.lich"]))
    assert r["lich_ics.lich"]["hop_le"] is True
    assert asyncio.run(inst.trang_thai(["lich_ics.khong_co"])) == {}


# ── B. cai() ────────────────────────────────────────────────────────────

def test_cai_thieu_url_that_bai():
    inst = _module()
    r = asyncio.run(inst.cai({"ten_lich": "Lịch nhà"}))
    assert r["ok"] is False


def test_cai_so_luong_ten_url_lech_that_bai():
    inst = _module()
    r = asyncio.run(inst.cai({"ten_lich": "Một, Hai", "url_lich": ["http://a.invalid/x.ics"]}))
    assert r["ok"] is False
    assert "thứ tự" in r["loi"].lower() or "khớp" in r["loi"].lower()


def test_cai_url_khong_http_that_bai():
    inst = _module()
    r = asyncio.run(inst.cai({"ten_lich": "L", "url_lich": ["ftp://a.invalid/x.ics"]}))
    assert r["ok"] is False


def test_cai_url_loopback_bi_chan():
    """SSRF: URL trỏ loopback bị từ chối khi không phải môi trường test."""
    inst = _module()
    r = asyncio.run(inst.cai({"ten_lich": "L", "url_lich": ["http://127.0.0.1:9/x.ics"]}))
    assert r["ok"] is False
    assert "ssrf" in r["loi"].lower() or "chặn" in r["loi"].lower()


def test_cai_url_metadata_bi_chan():
    inst = _module()
    r = asyncio.run(inst.cai({"ten_lich": "L", "url_lich": ["http://169.254.169.254/x.ics"]}))
    assert r["ok"] is False


def test_cai_so_ngay_ngoai_khoang_that_bai():
    inst = _module()
    for v in ("0", "32", "abc"):
        r = asyncio.run(inst.cai({"ten_lich": "L", "url_lich": ["http://a.invalid/x.ics"],
                                          "so_ngay_mac_dinh": v}))
        assert r["ok"] is False, v


def test_cai_mui_gio_la_that_bai():
    inst = _module()
    r = asyncio.run(inst.cai({"ten_lich": "L", "url_lich": ["http://a.invalid/x.ics"],
                              "mui_gio": "Mars/Olympus"}))
    assert r["ok"] is False


def test_cai_ok_voi_may_chu_gia(loopback_cho_phep):
    with MayChuICSGia() as srv:
        inst = _module()
        r = asyncio.run(inst.cai(_cau_hinh(srv.url)))
        assert r["ok"] is True, r
        assert r["cau_hinh_luu"]["so_lich"] == 1


def test_cai_url_tra_html_that_bai(loopback_cho_phep):
    with MayChuICSGia("<html>đăng nhập</html>", tieu_de={"Content-Type": "text/html"}) as srv:
        inst = _module()
        r = asyncio.run(inst.cai(_cau_hinh(srv.url)))
        assert r["ok"] is False
        assert "ICS" in r["loi"]


def test_cai_khong_lo_url_trong_loi(loopback_cho_phep):
    with MayChuICSGia("x", ma=404) as srv:
        inst = _module()
        r = asyncio.run(inst.cai({"ten_lich": "L", "url_lich": [srv.url]}))
        assert r["ok"] is False
        assert srv.url not in json.dumps(r, ensure_ascii=False)


# ── C. phân tích ICS (thuần, không mạng) ─────────────────────────────────

def test_phan_tich_dt_naive_ve_mui_gio_mac_dinh():
    lich = _tai_lich()
    dt, ca_ngay = lich.phan_tich_dt("DTSTART:20260215T090000", TZ_HN)
    assert ca_ngay is False
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 2, 15, 9)
    assert dt.utcoffset() == timedelta(hours=7)


def test_phan_tich_dt_utc():
    lich = _tai_lich()
    dt, _ = lich.phan_tich_dt("DTSTART:20260215T090000Z", TZ_HN)
    assert dt.astimezone(TZ_HN).hour == 16  # 09:00 UTC = 16:00 +07


def test_phan_tich_dt_tzid():
    """TZID phải được tôn trọng (c2a bỏ qua, gán múi giờ mặc định — port này sửa)."""
    lich = _tai_lich()
    dt, _ = lich.phan_tich_dt("DTSTART;TZID=America/New_York:20260215T090000", TZ_HN)
    assert dt.utcoffset() == timedelta(hours=-5)  # EST tháng 2
    assert dt.astimezone(TZ_HN).hour == 21


def test_phan_tich_dt_tzid_la_ve_mac_dinh():
    lich = _tai_lich()
    dt, _ = lich.phan_tich_dt("DTSTART;TZID=Mars/Olympus:20260215T090000", TZ_HN)
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=7)  # không vỡ, về mặc định


def test_phan_tich_dt_ngay_ca_ngay_va_nam_nhuan():
    lich = _tai_lich()
    dt, ca_ngay = lich.phan_tich_dt("DTSTART;VALUE=DATE:20240229", TZ_HN)
    assert ca_ngay is True
    assert (dt.year, dt.month, dt.day) == (2024, 2, 29)  # 2024 nhuận


def test_phan_tich_dt_sai_tra_none():
    lich = _tai_lich()
    dt, _ = lich.phan_tich_dt("DTSTART:khong-phai-ngay", TZ_HN)
    assert dt is None


def test_phan_tich_su_kien_co_ban():
    lich = _tai_lich()
    sk = lich.phan_tich_ics(ICS_CO_BAN, TZ_HN)
    assert len(sk) == 3
    assert sk[0]["tieu_de"] == "Họp nhóm"
    assert sk[0]["dia_diem"] == "Phòng A"
    assert sk[1]["bat_dau"].astimezone(TZ_HN).hour == 21  # 14:00Z
    assert sk[2]["ca_ngay"] is True


def test_ky_tu_dieu_khien_bi_loc():
    """XSS/điều khiển: ký tự điều khiển trong tiêu đề/mô tả bị lọc."""
    lich = _tai_lich()
    ics = ICS_CO_BAN.replace("Họp nhóm", "Họp\x00nhóm<img\x1b>")
    sk = lich.phan_tich_ics(ics, TZ_HN)
    assert "\x00" not in sk[0]["tieu_de"]
    assert "\x1b" not in sk[0]["tieu_de"]
    assert "<img" in sk[0]["tieu_de"]  # dữ liệu giữ nguyên, JSON vận chuyển an toàn


def test_tran_kich_thuoc_tep():
    lich = _tai_lich()
    lon = "BEGIN:VCALENDAR\n" + "X:" + "a" * (lich.TRAN_TEP_BYTES + 1) + "\nEND:VCALENDAR"
    with pytest.raises(lich.LoiLich) as e:
        lich.phan_tich_ics(lon, TZ_HN)
    assert e.value.ma == "tep_qua_lon"


def test_tran_so_su_kien():
    lich = _tai_lich()
    mot = "BEGIN:VEVENT\nUID:u\nDTSTART:20260215T090000\nSUMMARY:x\nEND:VEVENT\n"
    lon = "BEGIN:VCALENDAR\n" + mot * (lich.TRAN_SU_KIEN_TEP + 1) + "END:VCALENDAR"
    with pytest.raises(lich.LoiLich) as e:
        lich.phan_tich_ics(lon, TZ_HN)
    assert e.value.ma == "qua_nhieu_su_kien"


# ── D. RRULE ─────────────────────────────────────────────────────────────

def _ics_rrule(rrule: str, dtstart: str = "DTSTART:20260202T090000", exdate: str = ""):
    return (f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:r1\nDTSTAMP:20260101T000000Z\n"
            f"{dtstart}\nRRULE:{rrule}\n{exdate}SUMMARY:Lặp\nEND:VEVENT\nEND:VCALENDAR")


def test_rrule_daily_count():
    lich = _tai_lich()
    sk = lich.phan_tich_ics(_ics_rrule("FREQ=DAILY;COUNT=3"), TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk[0], den)
    assert len(moc) == 3
    assert moc[1].day == 3 and moc[2].day == 4


def test_rrule_until_duoc_ton_trong():
    lich = _tai_lich()
    sk = lich.phan_tich_ics(_ics_rrule("FREQ=DAILY;UNTIL=20260204T090000"), TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    assert len(lich.mo_rong(sk[0], den)) == 3  # 2,3,4


def test_rrule_interval():
    lich = _tai_lich()
    sk = lich.phan_tich_ics(_ics_rrule("FREQ=DAILY;INTERVAL=2;COUNT=3"), TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk[0], den)
    assert [m.day for m in moc] == [2, 4, 6]


def test_rrule_weekly_byday():
    lich = _tai_lich()
    sk = lich.phan_tich_ics(_ics_rrule("FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4"), TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk[0], den)
    assert len(moc) == 4
    assert {m.weekday() for m in moc} == {0, 2}  # thứ 2, thứ 4


def test_rrule_freq_gio_khong_mo_rong():
    """«Lặp mỗi giây»: FREQ dưới DAILY bị bỏ qua, coi như sự kiện đơn."""
    lich = _tai_lich()
    for freq in ("SECONDLY", "MINUTELY", "HOURLY"):
        sk = lich.phan_tich_ics(_ics_rrule(f"FREQ={freq};COUNT=10"), TZ_HN)
        den = datetime(2026, 3, 1, tzinfo=TZ_HN)
        assert len(lich.mo_rong(sk[0], den)) == 1, freq


def test_rrule_di_dang_khong_vo():
    lich = _tai_lich()
    sk = lich.phan_tich_ics(_ics_rrule("FREQ=DAILY;COUNT=abc"), TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    assert len(lich.mo_rong(sk[0], den)) == 1  # RRULE hỏng → sự kiện đơn
    sk2 = lich.phan_tich_ics(_ics_rrule("FREQ=WEEKLY;BYDAY=XX;COUNT=3"), TZ_HN)
    assert len(lich.mo_rong(sk2[0], den)) == 1


def test_rrule_khong_ket_thuc_bi_cat():
    """Chống nổ lặp vô hạn: không COUNT/UNTIL → cắt ở trần."""
    lich = _tai_lich()
    sk = lich.phan_tich_ics(_ics_rrule("FREQ=DAILY"), TZ_HN)
    den = datetime(2027, 8, 1, tzinfo=TZ_HN)  # > 500 ngày: trần phải cắt trước
    moc = lich.mo_rong(sk[0], den)
    assert len(moc) == lich.TRAN_LAP_LAI


def test_exdate_loai_tru():
    lich = _tai_lich()
    ics = _ics_rrule("FREQ=DAILY;COUNT=3", exdate="EXDATE:20260203T090000\n")
    sk = lich.phan_tich_ics(ics, TZ_HN)
    den = datetime(2026, 3, 1, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk[0], den)
    assert len(moc) == 2
    assert all(m.day != 3 for m in moc)


def test_dst_khong_lech_moc_lap():
    """DST Mỹ 2026-03-08: lặp DAILY giữ nguyên GIỜ ĐỊA PHƯƠNG."""
    lich = _tai_lich()
    ics = _ics_rrule("FREQ=DAILY;COUNT=4",
                     dtstart="DTSTART;TZID=America/New_York:20260306T090000")
    sk = lich.phan_tich_ics(ics, TZ_HN)
    den = datetime(2026, 3, 15, tzinfo=TZ_HN)
    moc = lich.mo_rong(sk[0], den)
    assert len(moc) == 4
    for m in moc:
        assert m.astimezone(TZ_NY).hour == 9  # vẫn 9h sáng giờ New York


# ── E. thuc_hien qua máy chủ giả ─────────────────────────────────────────

def test_su_kien_sap_toi_qua_may_chu_gia(loopback_cho_phep):
    with MayChuICSGia(_ics_dong()) as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        r = asyncio.run(inst.cai(_cau_hinh(srv.url)))
        assert r["ok"] is True
        inst.nap(r["cau_hinh_luu"])
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi", {}))
        assert kq["ok"] is True
        sk = kq["ket_qua"]["su_kien"]
        assert len(sk) == 3
        assert sk[0]["tieu_de"] == "Họp nhóm"
        assert kq["ket_qua"]["nguon_ngoai"] is True  # dữ liệu ngoài: đánh dấu
        assert "bat_dau" in sk[0] and "ket_thuc" in sk[0]


def test_su_kien_sap_toi_gioi_han_so_ngay(loopback_cho_phep):
    with MayChuICSGia() as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        inst.nap(asyncio.run(inst.cai(_cau_hinh(srv.url)))["cau_hinh_luu"])
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi",
                                        {"so_ngay": 1, "toi_da": 30}))
        assert kq["ok"] is True
        assert kq["ket_qua"]["su_kien"] == []


def test_tham_so_ngoai_khoang_bi_tu_choi(loopback_cho_phep):
    with MayChuICSGia() as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        inst.nap(asyncio.run(inst.cai(_cau_hinh(srv.url)))["cau_hinh_luu"])
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi", {"so_ngay": 99}))
        assert kq["ok"] is False


def test_lich_khong_ton_tai(loopback_cho_phep):
    with MayChuICSGia() as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        inst.nap(asyncio.run(inst.cai(_cau_hinh(srv.url)))["cau_hinh_luu"])
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi",
                                        {"lich": "khong-co-lich-nay"}))
        assert kq["ok"] is False
        assert "khong_thay" in kq["loi"] or "Không thấy" in kq["loi"]


def test_thuc_the_la_va_hanh_dong_la():
    mod = _tai_module()
    inst = mod.Module(_manifest())
    inst.nap(_cau_hinh("http://example.invalid/x.ics"))
    r = asyncio.run(inst.thuc_hien("lich_ics.khong_co", "su_kien_sap_toi", {}))
    assert r["ok"] is False
    r = asyncio.run(inst.thuc_hien("lich_ics.lich", "xoa_lich", {}))
    assert r["ok"] is False


def test_cache_khong_goi_mang_lai(loopback_cho_phep):
    with MayChuICSGia() as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        inst.nap(asyncio.run(inst.cai(_cau_hinh(srv.url, cache_giay="900")))["cau_hinh_luu"])
        n0 = srv.so_luot_goi
        asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi", {}))
        asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi", {}))
        assert srv.so_luot_goi == n0  # cai() đã tải 1 lần, 2 lượt gọi sau dùng cache


def test_kiem_tra_lich_ok(loopback_cho_phep):
    with MayChuICSGia(_ics_dong()) as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        inst.nap(asyncio.run(inst.cai(_cau_hinh(srv.url)))["cau_hinh_luu"])
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "kiem_tra_lich", {}))
        assert kq["ok"] is True
        assert kq["ket_qua"]["tong_trong_tep"] == 3
        assert len(kq["ket_qua"]["xem_truoc"]) == 3
        assert srv.url not in json.dumps(kq, ensure_ascii=False)  # URL bí mật không lộ


def test_kiem_tra_lich_404(loopback_cho_phep):
    with MayChuICSGia("không thấy", ma=404) as srv:
        # cai() với 404 thất bại (đã kiểm ở test_cai_khong_lo_url_trong_loi);
        # nạp tay để kiểm thuc_hien.
        mod = _tai_module()
        inst2 = mod.Module(_manifest())
        inst2.nap({"lich": [{"id": "l1", "ten": "L", "url": srv.url, "bat": True}],
                   "so_ngay_mac_dinh": 7, "toi_da_mac_dinh": 30,
                   "cache_giay": 900, "mui_gio": "Asia/Ho_Chi_Minh"})
        kq = asyncio.run(inst2.thuc_hien("lich_ics.lich", "kiem_tra_lich", {}))
        assert kq["ok"] is False
        assert "404" in kq["loi"]


def test_chuyen_huong_ra_metadata_bi_chan(loopback_cho_phep):
    """SSRF qua chuyển hướng: 302 sang 169.254.169.254 bị chặn."""
    with MayChuICSGia(chuyen_huong="http://169.254.169.254/latest/meta-data/") as srv:
        mod = _tai_module()
        inst = mod.Module(_manifest())
        inst.nap({"lich": [{"id": "l1", "ten": "L", "url": srv.url, "bat": True}],
                  "so_ngay_mac_dinh": 7, "toi_da_mac_dinh": 30,
                  "cache_giay": 900, "mui_gio": "Asia/Ho_Chi_Minh"})
        kq = asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi", {}))
        assert kq["ok"] is False
        assert "chuyen_huong" in kq["loi"].lower() or "chặn" in kq["loi"].lower()


def test_url_bi_mat_khong_vao_log(loopback_cho_phep, caplog):
    bi_mat = "bi-mat-lich-xyz123"
    with MayChuICSGia() as srv:
        url = srv.url.replace("lich.ics", f"{bi_mat}.ics")
        mod = _tai_module()
        inst = mod.Module(_manifest())
        with caplog.at_level(logging.DEBUG):
            r = asyncio.run(inst.cai(_cau_hinh(url)))
            assert r["ok"] is True
            inst.nap(r["cau_hinh_luu"])
            asyncio.run(inst.thuc_hien("lich_ics.lich", "su_kien_sap_toi", {}))
            logging.getLogger("httpx").warning("thử %s", url)
    assert bi_mat not in caplog.text


def test_bo_loc_ton_tai_sau_nap_lai(loopback_cho_phep):
    """Mẫu NV116: nạp module dưới tên gói khác không «cướp» bộ lọc của bản cũ.

    Đếm TƯƠNG ĐỐI: tiến trình test có thể đã nạp module dưới tên gói mặc định
    ở test trước; điều cần giữ là mỗi bản nạp thêm ĐÚNG bộ lọc của mình và
    không bản nào bị gỡ."""
    def _locs():
        return [f for f in logging.getLogger("httpx").filters
                if getattr(f, "_lich_ics_loc", False)]

    truoc = _locs()
    mod1 = _tai_module("modules_lich_ics_pkg_a")
    mod2 = _tai_module("modules_lich_ics_pkg_b")
    with MayChuICSGia() as srv:
        for mod in (mod1, mod2):
            inst = mod.Module(_manifest())
            r = asyncio.run(inst.cai(_cau_hinh(srv.url)))
            assert r["ok"] is True
    locs = _locs()
    assert len(locs) == len(truoc) + 2
    assert all(f in locs for f in truoc)  # bộ lọc cũ còn nguyên
    assert {"modules_lich_ics_pkg_a", "modules_lich_ics_pkg_b"} <= {
        type(f).__module__ for f in locs}


def test_suc_khoe_khong_goi_mang_va_khong_lo_bi_mat():
    mod = _tai_module()
    inst = mod.Module(_manifest())
    inst.nap(_cau_hinh("http://example.invalid/bi-mat-abc.ics"))
    sk = asyncio.run(inst.suc_khoe())
    assert sk["ok"] is True
    assert "bi-mat-abc" not in json.dumps(sk, ensure_ascii=False)
