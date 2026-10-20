"""Module tien-ich-doi-song (NV165): bản tin tóm tắt, chỉ đường, vùng radar, dấu câu.

Port c2a (d879b03, CHỈ ĐỌC): digest.py, chi_duong.py, vung_khoang_cach.py,
cham_cau.py. KHÔNG port trong_xe.py (phụ thuộc camera/nhin-nha, ⚠ chờ chủ máy).

Đỏ trước: chạy file này trước khi có modules/tien-ich-doi-song → ImportError
(không có mã). Xanh sau: port engine + module chuẩn C2 thiet_bi.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "tien-ich-doi-song"

DIA_CHI_DAU = "dia-chi-{{{NEVER-LOG-THIS}}}-bi-mat"


def _nap_thu_cong(ten_ghi: str, tep: str):
    """Nạp một tệp .py của module như module độc lập (thư mục có dấu gạch ngang)."""
    spec = importlib.util.spec_from_file_location(
        ten_ghi, THU_MUC / tep, submodule_search_locations=[str(THU_MUC)])
    md = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[ten_ghi] = md
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def goi(tmp_path, monkeypatch):
    """Biến môi trường dữ liệu trỏ vào tmp; trả (digest, vung)."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    dg = _nap_thu_cong("tids_digest_test", "digest.py")
    vg = _nap_thu_cong("tids_vung_test", "vung.py")
    dg.dat_duong_du_lieu(tmp_path / "data" / "du_lieu" / "tien-ich-doi-song")
    vg.dat_duong_du_lieu(tmp_path / "data" / "du_lieu" / "tien-ich-doi-song")
    return dg, vg


@pytest.fixture()
def cd():
    return _nap_thu_cong("tids_chiduong_test", "chi_duong.py")


@pytest.fixture()
def cc():
    return _nap_thu_cong("tids_chamcau_test", "cham_cau.py")


def _module(tmp_path, monkeypatch, bat_tim_duong="1"):
    from app.kham_pha import doc_manifest, nap_ma, van_tay
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    lop, _ = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    mod = lop(m)
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod.nap({"bat_tim_duong": bat_tim_duong})
    return mod


def chay(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── digest: máy trạng thái ───────────────────────────────────────────────────

def test_digest_queue_va_dem(goi):
    dg, _ = goi
    dg.queue("lich", "Họp 9h")
    dg.queue("lich", "Họp 10h")
    dg.queue("email", "Thư A")
    assert dg.pending_count("lich") == 2
    assert dg.pending_count("email") == 1
    assert dg.pending_count("khong-co") == 0


def test_digest_seen_chong_trung(goi):
    dg, _ = goi
    assert dg.seen("email", "uid-1") is False
    dg.mark_seen("email", "uid-1")
    assert dg.seen("email", "uid-1") is True
    dg.mark_seen("email", "uid-1")  # trùng vẫn ok, không nhân đôi
    assert dg.seen("email", "") is False


def test_digest_queue_cat_tran(goi):
    dg, _ = goi
    dg.queue("x", "A" * 5000)
    ban = dg.flush("x")
    assert ban["so_muc"] == 1
    assert len(ban["muc"][0]["noi_dung"]) == 2000  # trần bộ đệm


def test_digest_flush_gop_ban_tin(goi):
    dg, _ = goi
    dg.queue("lich", "Họp 9h sáng")
    dg.queue("lich", "Nộp báo cáo")
    ban = dg.flush("lich", title="Tin hôm nay")
    assert ban["so_muc"] == 2
    assert "Họp 9h sáng" in ban["van_ban"] and "Nộp báo cáo" in ban["van_ban"]
    assert dg.pending_count("lich") == 0  # đã lấy ra thì hết


def test_digest_flush_rong_khong_spam(goi):
    dg, _ = goi
    ban = dg.flush("lich")
    assert ban["so_muc"] == 0 and ban["van_ban"] == ""


def test_digest_tick_moi_moc_mot_lan_mot_ngay(goi):
    dg, _ = goi
    gio_hien_tai = dg._hm_now()
    dg.queue("lich", "Mục A")
    nguon = [("lich", {"notify_times": [gio_hien_tai], "notify_targets": ["kenh1"]}, "Tin hôm nay")]
    lan1 = dg.tick(nguon)
    assert len(lan1) == 1 and lan1[0]["so_muc"] == 1
    assert lan1[0]["dich"] == ["kenh1"]
    dg.queue("lich", "Mục B đến sau")  # có mục mới vẫn không bắn thêm trong ngày
    lan2 = dg.tick(nguon)  # cùng phút, đã bắn hôm nay → không bắn nữa
    assert lan2 == []
    assert dg.pending_count("lich") == 1  # mục B còn nguyên chờ hôm sau


def test_digest_tick_gio_sai_khong_ban(goi):
    dg, _ = goi
    dg.queue("lich", "Mục A")
    nguon = [("lich", {"notify_times": ["99:99", "ab:cd"], "notify_targets": []}, "T")]
    assert dg.tick(nguon) == []  # giờ sai khuôn → không bao giờ tới giờ


def test_digest_norm_hm(goi):
    dg, _ = goi
    assert dg._norm_hm("7:5") == "07:05"
    assert dg._norm_hm("0705") == "07:05"
    assert dg._norm_hm("07:05") == "07:05"
    assert dg._norm_hm("25:00") == ""
    assert dg._norm_hm("abc") == ""


def test_digest_tom_tat_ngan_giu_nguyen(goi):
    dg, _ = goi
    assert dg.summarize("Ngắn thôi") == "Ngắn thôi"
    assert dg.summarize("") == ""


def test_digest_tom_tat_cat_gon_khong_bia(goi):
    dg, _ = goi
    dai = "Tin dài. " * 500
    ra = dg.summarize(dai, what="email", max_chars=900)
    assert len(ra) <= 901 and ra.endswith("…")
    assert "Tin dài." in ra  # không thêm nội dung mới


def test_digest_tom_tat_voi_model_gia(goi):
    dg, _ = goi
    goi_model = lambda model, messages: "- Ý 1\n- Ý 2"
    ra = dg.summarize("Tin dài. " * 500, goi_model=goi_model)
    assert ra == "- Ý 1\n- Ý 2"


def test_digest_notify_gui_ngay_va_xep_cho(goi):
    dg, _ = goi
    cfg = {"notify_targets": ["kenh1"], "notify_on_new": True, "notify_times": ["07:00"]}
    ra = dg.notify("email", cfg, "Thư mới")
    assert ra["gui_ngay"] == ["Thư mới"]
    assert ra["dich"] == ["kenh1"]
    assert ra["da_xep_cho"] is True
    assert dg.pending_count("email") == 1


def test_digest_forget_xoa_nguon(goi):
    dg, _ = goi
    dg.queue("email", "A")
    dg.forget("email")
    assert dg.pending_count("email") == 0


# ── chi_duong: thuần ─────────────────────────────────────────────────────────

def test_chi_duong_phuong_tien(cd):
    assert cd.chuan_phuong_tien("xe máy") == ("driving", "driving", True)
    assert cd.chuan_phuong_tien("Ô TÔ") == ("driving", "driving", True)
    assert cd.chuan_phuong_tien("xe buýt") == ("", "transit", False)
    assert cd.chuan_phuong_tien("tàu vũ trụ") == ("driving", "driving", True)  # lạ → xe máy
    assert cd.MENU_PHUONG_TIEN == ["xe máy", "ô tô", "đi bộ", "xe buýt"]


def test_chi_duong_bo_ma_toa(cd):
    assert cd._bo_ma_toa("CT4B X2 Bắc Linh Đàm, Hoàng Liệt, Hà Nội") == "Bắc Linh Đàm, Hoàng Liệt, Hà Nội"
    assert cd._bo_ma_toa("Tòa nhà CT4Bx2 Bắc Linh Đàm") == "Bắc Linh Đàm"
    assert cd._bo_ma_toa("123 Mai Hắc Đế, Hà Nội") == "123 Mai Hắc Đế, Hà Nội"  # số nhà giữ


def test_chi_duong_maps_link_ma_hoa(cd):
    link = cd.maps_link("A&B", "C<script>", "driving")
    assert "<script>" not in link and "A&B" not in link  # đã urlencode
    assert "google.com/maps/dir/" in link


def test_chi_duong_mo_ta_buoc(cd):
    buoc = cd._mo_ta_buoc({"maneuver": {"type": "turn", "modifier": "left"},
                           "name": "Mai Hắc Đế", "distance": 150})
    assert "Rẽ trái Mai Hắc Đế (~150 m)" == buoc
    buoc2 = cd._mo_ta_buoc({"maneuver": {}, "name": "", "distance": 0})
    assert buoc2 == "Đi tiếp"  # thiếu maneuver không sập
    buoc3 = cd._mo_ta_buoc({"maneuver": {"type": "depart", "modifier": "left"},
                            "name": "", "distance": 2500})
    assert buoc3 == "Xuất phát trên (~2.5 km)"  # depart không gắn hướng vào đọc ngược


def test_chi_duong_chuan_tinh(cd):
    assert cd._chuan_tinh("Thành phố Hà Nội") == cd._chuan_tinh("Hà Nội")
    assert cd._chuan_tinh("Tỉnh Đà Nẵng") != cd._chuan_tinh("Hà Nội")


def test_chi_duong_ghim_maps_offline(cd):
    assert cd.la_ghim_maps("https://maps.app.goo.gl/abc123") is True
    assert cd.la_ghim_maps("https://www.google.com/maps/place/Bờ+Hồ/@21.028,105.852,17z") is True
    assert cd.la_ghim_maps("https://www.google.com/maps/search/?query=hồ+gươm") is False
    assert cd.la_ghim_maps("114 Mai Hắc Đế") is False
    toa = cd._toa_do_tu_url_maps("https://www.google.com/maps/@21.028511,105.852153,17z")
    assert toa == (21.028511, 105.852153)
    assert cd._giai_link_maps("không phải link") is None


class _GiaHttpx:
    """Giả httpx.get / Client cho test mạng (không chạm mạng thật)."""

    def __init__(self):
        self.host_da_goi: list[str] = []

    def get(self, url, **kw):
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        self.host_da_goi.append(host)
        if "nominatim.openstreetmap.org/search" in url:
            return _GiaPhanHoi([{"lat": "21.0285", "lon": "105.8522",
                                 "display_name": "Mai Hắc Đế, Hà Nội",
                                 "address": {"city": "Hà Nội"}}])
        if "nominatim.openstreetmap.org/reverse" in url:
            return _GiaPhanHoi({"display_name": "Bờ Hồ, Hoàn Kiếm, Hà Nội, Việt Nam, 10000"})
        if "router.project-osrm.org" in url:
            return _GiaPhanHoi({"code": "Ok", "routes": [{
                "distance": 1200, "duration": 300,
                "legs": [{"steps": [{"maneuver": {"type": "turn", "modifier": "right"},
                                     "name": "Mai Hắc Đế", "distance": 200}]}]}]})
        raise AssertionError(f"host lạ: {url}")


class _GiaPhanHoi:
    def __init__(self, du_lieu):
        self._du_lieu = du_lieu
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._du_lieu


@pytest.fixture()
def mang_gia(cd, monkeypatch):
    gia = _GiaHttpx()
    monkeypatch.setattr("httpx.get", gia.get)
    return gia


def test_chi_duong_geocode_mang_gia(cd, mang_gia):
    kq = cd.geocode("114 Mai Hắc Đế, Hà Nội")
    assert kq is not None
    lat, lon, ten, tinh, chinh_xac = kq
    assert (lat, lon) == (21.0285, 105.8522)
    assert tinh == "Hà Nội" and chinh_xac is True
    assert set(mang_gia.host_da_goi) <= {"nominatim.openstreetmap.org"}


def test_chi_duong_chi_duong_mang_gia(cd, mang_gia):
    kq = cd.chi_duong("Bờ Hồ, Hà Nội", "114 Mai Hắc Đế, Hà Nội", "xe máy")
    assert kq["ok"] is True
    assert kq["km"] == 1.2 and kq["phut"] == 5
    assert kq["buoc"] == ["Rẽ phải Mai Hắc Đế (~200 m)"]
    assert kq["link"].startswith("https://www.google.com/maps/dir/")
    assert set(mang_gia.host_da_goi) <= {"nominatim.openstreetmap.org", "router.project-osrm.org"}


def test_chi_duong_xe_buyt_chi_link(cd, mang_gia):
    kq = cd.chi_duong("A", "B", "xe buýt")
    assert kq["ok"] is False and kq["ly_do"] == "khong_dinh_tuyen"
    assert "maps/dir/" in kq["link"]


def test_chi_duong_tim_dia_diem(cd, mang_gia):
    kq = cd.tim_dia_diem("Mai Hắc Đế")
    assert len(kq) == 1
    assert kq[0]["link"].startswith("https://www.google.com/maps/search/")
    assert kq[0]["tinh"] == "Hà Nội"


def test_chi_duong_ten_tu_toa_do(cd, mang_gia):
    ten = cd._ten_tu_toa_do(21.0285, 105.8522)
    assert ten == "Bờ Hồ, Hoàn Kiếm, Hà Nội"  # bỏ đuôi mã bưu chính + Việt Nam


def test_chi_duong_khong_log_dia_chi(cd, mang_gia, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="web2api.tien_ich_doi_song.chi_duong"):
        cd.geocode(DIA_CHI_DAU)
        cd.chi_duong(DIA_CHI_DAU, DIA_CHI_DAU)
    assert DIA_CHI_DAU not in caplog.text  # địa chỉ không vào log


def test_chi_duong_giai_link_chan_host_la(cd, monkeypatch):
    class _GiaClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, **k):
            class R: url = "https://evil.example.invalid/maps/@21.028511,105.852153,17z"
            return R()
    monkeypatch.setattr("httpx.Client", _GiaClient)
    assert cd._giai_link_maps("https://maps.app.goo.gl/rutgon") is None  # đích lạ dù có toạ độ → bỏ


def test_chi_duong_geocode_bo_ma_toa_fallback(cd, monkeypatch):
    def get_gia(url, **kw):
        q = str(kw.get("params", {}).get("q", "")) + url
        if "CT4B" in q:
            return _GiaPhanHoi([])  # địa chỉ đầy đủ có mã toà → không ra
        return _GiaPhanHoi([{"lat": "20.9800", "lon": "105.7900",
                             "display_name": "Bắc Linh Đàm, Hà Nội",
                             "address": {"city": "Hà Nội"}}])
    monkeypatch.setattr("httpx.get", get_gia)
    kq = cd.geocode("CT4B X2 Bắc Linh Đàm, Hoàng Liệt, Hà Nội")
    assert kq is not None
    assert kq[4] is False  # phải BỎ MÃ TOÀ mới ra → chính xác False
    assert "Bắc Linh Đàm" in kq[2]  # dùng truy vấn rút gọn mới ra kết quả


def test_chi_duong_tinh_lech_khong_dinh_tuyen(cd, monkeypatch):
    def get_gia(url, **kw):
        q = str(kw.get("params", {}).get("q", "")) + url
        if "nominatim.openstreetmap.org/search" in url:
            if "danang.example" in q:
                return _GiaPhanHoi([{"lat": "16.0544", "lon": "108.2022",
                                     "display_name": "Cầu Rồng, Đà Nẵng",
                                     "address": {"city": "Đà Nẵng"}}])
            return _GiaPhanHoi([{"lat": "21.0285", "lon": "105.8522",
                                 "display_name": "Bờ Hồ, Hà Nội",
                                 "address": {"city": "Hà Nội"}}])
        return _GiaPhanHoi({"code": "Ok", "routes": []})
    monkeypatch.setattr("httpx.get", get_gia)
    kq = cd.chi_duong("danang.example Cầu Rồng", "Bờ Hồ Hà Nội", "xe máy")
    assert kq["ok"] is False and kq["ly_do"] == "tinh_khong_khop"


# ── vung: logic học ──────────────────────────────────────────────────────────

def test_vung_nguong_tach_dung(goi):
    _, vg = goi
    trong = [2.3, 2.4, 2.5, 2.6, 2.7] * 30
    ngoai = [4.3, 4.4, 4.5, 4.6, 4.7] * 30
    kq = vg.nguong(trong, ngoai)
    assert kq["huong"] == "duoi" and 2.7 <= kq["nguong"] <= 4.3
    assert kq["dung"] >= 0.9


def test_vung_nguong_thieu_nhan(goi):
    _, vg = goi
    assert vg.nguong([], [1.0]) == {"dung": 0.0}
    assert vg.nguong([1.0], []) == {"dung": 0.0}


def test_vung_tri_trang_thai_khi_chua_hoc(goi):
    _, vg = goi
    assert vg.trong_vung("ma-la", 99.0) is True   # không bao giờ nói «trống» vì thiếu hiểu biết
    assert vg.vi_tri("ma-la", 99.0) is None
    assert vg.trong_vung("ma-la", 0) is True      # số 0 = radar không đo được
    assert vg.trong_vung("ma-la", "xau") is True


def test_vung_dat_nguong_sai(goi):
    _, vg = goi
    with pytest.raises(ValueError):
        vg.dat_nguong("ma-khong-co", 3.0)
    assert vg.vung_dang_dung(None) is None


def test_vung_hoc_tu_du_lieu_tong_hop(goi):
    import time as _time
    _, vg = goi
    # Radar phòng khách: camera thấy người (trong khu) → số đo ~2.5;
    # camera không thấy + radar bếp báo (khu bên) → số đo ~4.4.
    t0 = _time.time() - 5 * 86400
    kc, r_ct, cam_ct, ke_ct = [], [], [[]], {"radar_bep": []}
    for i in range(120):
        t = t0 + i * 60
        kc.append((t, "2.5")); r_ct.append((t, "on")); cam_ct[0].append((t, "on"))
        ke_ct["radar_bep"].append((t, "off"))
    for i in range(120, 240):
        t = t0 + i * 60
        kc.append((t, "4.4")); r_ct.append((t, "on")); cam_ct[0].append((t, "off"))
        ke_ct["radar_bep"].append((t, "on"))
    kq = vg.hoc_tu_du_lieu("radar_pk", "phong_khach", "binary_sensor.pk_radar",
                           "m", kc, r_ct, cam_ct, ke_ct)
    assert kq["dat"] is True
    assert kq["huong"] == "duoi"
    assert 2.5 <= kq["nguong"] <= 4.4
    assert "radar_bep" in kq["ke"]  # nguồn báo lây phát hiện được
    assert vg.trong_vung("radar_pk", 2.0) is True
    assert vg.trong_vung("radar_pk", 5.0) is False
    assert vg.vi_tri("radar_pk", 5.0) is False


def test_vung_hoc_chua_du_mau_khong_dat(goi):
    _, vg = goi
    t0 = 1_700_000_000.0
    kc = [(t0 + i, "2.5") for i in range(10)]
    r_ct = [(t0 + i, "on") for i in range(10)]
    cam_ct = [[(t0 + i, "on") for i in range(10)]]
    kq = vg.hoc_tu_du_lieu("radar_pk", "pk", "r", "m", kc, r_ct, cam_ct, {})
    assert kq["dat"] is False
    assert vg.trong_vung("radar_pk", 99.0) is True  # chưa đạt → luôn đúng


def test_vung_nguong_chu_song_qua_hoc(goi):
    _, vg = goi
    t0 = 1_700_000_000.0
    kc = [(t0 + i, "2.5") for i in range(200)]
    r_ct = [(t0 + i, "on") for i in range(200)]
    cam = [[(t0 + i, "on") for i in range(200)]]
    vg.hoc_tu_du_lieu("r1", "pk", "r", "m", kc, r_ct, cam, {})
    vg.dat_nguong("r1", 3.5)
    vg.hoc_tu_du_lieu("r1", "pk", "r", "m", kc, r_ct, cam, {})
    assert vg.vung_dang_dung(vg.ds()["r1"]) == ("duoi", 3.5)


def test_vung_danh_sach_va_ti_le_bat(goi):
    _, vg = goi
    assert vg.ti_le_bat([(0.0, "on"), (10.0, "off")], 0.0, 20.0) == 0.5
    assert vg.ds() == {}


# ── cham_cau: thuần ──────────────────────────────────────────────────────────

def test_cham_cau_thieu_dau_cau(cc):
    assert cc.thieu_dau_cau(["xin chào các bạn hôm nay chúng ta " * 30]) is True
    assert cc.thieu_dau_cau(["Xin chào. Hôm nay đẹp trời!"]) is False
    assert cc.thieu_dau_cau(["ngắn"]) is False  # dưới KY_TU_TOI_THIEU


def test_cham_cau_chia_lo(cc):
    manh = ["a" * 1000, "b" * 1000, "c" * 100]
    lo = cc._chia_lo(manh)
    assert sum(len(x) for x in lo) == 3 and len(lo) >= 1
    assert cc._chia_lo([]) == []


def _goi_model_gia(model, messages):
    chu = messages[-1]["content"]
    return chu.replace("xin chào", "Xin chào.").replace("hôm nay", "hôm nay,") + "."


def test_cham_cau_phuc_hoi_giu_nguyen_chu(cc):
    manh = ["xin chào các bạn", "hôm nay trời đẹp"]
    ra = cc.phuc_hoi(manh, "burst", _goi_model_gia)
    assert len(ra) == len(manh)
    goc = " ".join(manh).split()
    moi = " ".join(ra).split()
    assert [cc._khoa(t) for t in goc] == [cc._khoa(t) for t in moi]  # chữ không đổi


def test_cham_cau_model_doi_chu_giu_goc(cc):
    goi = lambda model, messages: "hoàn toàn khác hẳn luôn rồi nhé"
    manh = ["xin chào các bạn", "hôm nay trời đẹp"]
    assert cc.phuc_hoi(manh, "burst", goi) == manh  # model đổi chữ → giữ gốc


def test_cham_cau_model_loi_giu_goc(cc):
    def goi(model, messages):
        raise RuntimeError("mạng sập")
    manh = ["xin chào các bạn"]
    assert cc.phuc_hoi(manh, "burst", goi) == manh


# ── Module: hợp đồng C2 ──────────────────────────────────────────────────────

def test_manifest_hop_le():
    from app.kham_pha import doc_manifest
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    assert m["id"] == "tien-ich-doi-song"
    assert m["tien_to"] == "tienich"
    assert m["cung_cap"] == ["thiet_bi"]
    assert m["hang"] == "A"
    assert "httpx" in m["yeu_cau_goi"]
    hosts = m["tai_nguyen"]["mang_ra"]
    assert "nominatim.openstreetmap.org" in hosts and "router.project-osrm.org" in hosts


def test_thuc_the_4_thuc_the(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    ds = chay(mod.thuc_the(None))
    ids = [e["id"] for e in ds]
    assert ids == ["tienich.tin_tuc", "tienich.chi_duong", "tienich.vung", "tienich.dau_cau"]
    for e in ds:
        assert e["loai"] == "cong_cu"
        assert e["rui_ro_mac_dinh"] in ("doc", "ghi")
        for tn in e["tinh_nang"]:
            assert tn["tham_so"]["additionalProperties"] is False


def test_trang_thai(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    tt = chay(mod.trang_thai(["tienich.tin_tuc", "tienich.la"]))
    assert tt["tienich.tin_tuc"]["hop_le"] is True
    assert "tienich.la" not in tt


def test_thuc_hien_sai_thuc_the_va_hanh_dong(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    assert chay(mod.thuc_hien("tienich.khong_co", "x", {}))["ok"] is False
    assert chay(mod.thuc_hien("tienich.tin_tuc", "khong_co", {}))["ok"] is False
    assert chay(mod.thuc_hien("tienich.tin_tuc", "them_muc", {}))["ok"] is False  # thiếu tham số


def test_dong_luan_tin_tuc(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    ra = chay(mod.thuc_hien("tienich.tin_tuc", "them_muc",
                            {"nguon": "lich", "noi_dung": "Họp 9h", "ma_muc": "m1"}))
    assert ra["ok"] is True and ra["ket_qua"]["so_muc_cho"] == 1
    trung = chay(mod.thuc_hien("tienich.tin_tuc", "them_muc",
                               {"nguon": "lich", "noi_dung": "Họp 9h", "ma_muc": "m1"}))
    assert trung["ket_qua"]["trung"] is True  # chống trùng theo ma_muc
    dem = chay(mod.thuc_hien("tienich.tin_tuc", "so_muc_dang_cho", {"nguon": "lich"}))
    assert dem["ket_qua"]["so_muc_cho"] == 1
    tong = chay(mod.thuc_hien("tienich.tin_tuc", "tong_hop", {"nguon": "lich", "tieu_de": "T"}))
    assert tong["ket_qua"]["so_muc"] == 1 and "Họp 9h" in tong["ket_qua"]["van_ban"]


def test_moc_gio_qua_cong_cu(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    chay(mod.thuc_hien("tienich.tin_tuc", "them_muc", {"nguon": "lich", "noi_dung": "A"}))
    from modules import __path__  # noqa: F401
    import importlib.util
    spec = importlib.util.spec_from_file_location("tids_dg_gio", THU_MUC / "digest.py",
                                                  submodule_search_locations=[str(THU_MUC)])
    dg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dg)
    gio = dg._hm_now()
    ra = chay(mod.thuc_hien("tienich.tin_tuc", "kiem_tra_moc_gio",
                            {"danh_sach": [{"nguon": "lich", "gio_gui": [gio], "tieu_de": "T"}]}))
    assert len(ra["ket_qua"]["ban_tin"]) == 1


def test_chi_duong_tat_khong_goi_mang(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch, bat_tim_duong="0")
    da_goi = []
    monkeypatch.setattr("httpx.get", lambda *a, **k: da_goi.append(a) or _GiaPhanHoi([]))
    ra = chay(mod.thuc_hien("tienich.chi_duong", "tim_duong",
                            {"diem_di": "A", "diem_den": "B"}))
    assert ra["ok"] is False and "đã tắt" in ra["loi"]
    assert da_goi == []  # tắt thì không gọi mạng


def test_chi_duong_qua_cong_cu_mang_gia(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    gia = _GiaHttpx()
    monkeypatch.setattr("httpx.get", gia.get)
    ra = chay(mod.thuc_hien("tienich.chi_duong", "tim_duong",
                            {"diem_di": "Bờ Hồ", "diem_den": "Mai Hắc Đế", "phuong_tien": "xe máy"}))
    assert ra["ok"] is True and ra["ket_qua"]["ok"] is True
    assert ra["ket_qua"]["km"] == 1.2


def test_chi_duong_toa_do_sai(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    ra = chay(mod.thuc_hien("tienich.chi_duong", "ten_tu_toa_do",
                            {"vi_do": 999, "kinh_do": 0}))
    assert ra["ok"] is False


def test_dau_cau_qua_cong_cu(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    ra = chay(mod.thuc_hien("tienich.dau_cau", "kiem_tra_dau_cau",
                            {"cac_manh": ["chữ trần " * 100]}))
    assert ra["ket_qua"]["thieu_dau_cau"] is True
    lo = chay(mod.thuc_hien("tienich.dau_cau", "chia_lo", {"cac_manh": ["a" * 100]}))
    assert lo["ket_qua"]["lo"] == [[0]]


def test_vung_qua_cong_cu(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    ra = chay(mod.thuc_hien("tienich.vung", "trong_vung", {"ma": "chua_hoc", "gia_tri": 9}))
    assert ra["ket_qua"]["gia_tri"] is True
    sai = chay(mod.thuc_hien("tienich.vung", "dat_nguong", {"ma": "chua_hoc", "nguong": 3}))
    assert sai["ok"] is False  # mã chưa học → ValueError → ok False
