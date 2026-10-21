"""NV195 — module `nha-tien-ich`: port 11 tệp services/ c2a (d879b03).

Test-first: mọi test dưới đây PHẢI ĐỎ trước khi có modules/nha-tien-ich/.
Dữ liệu nhà trong test là GIẢ hoàn toàn (192.0.2.x, tên giả, entity_id giả).

Sáu hàng rào an toàn bắt buộc (mỗi hàng rào có test RIÊNG):
 1. Thử khô mặc định + xác nhận quản trị; nút tắt-bật toàn bộ.
 2. Khoá/bếp/bình nóng lạnh không bao giờ tự chạy — theo LOẠI do HA báo
    (domain/device_class), loại lạ chưa biết ⇒ nguy hiểm (fail-closed).
 3. Chống lặp kịch bản (dấu vết chuỗi gốc + giới hạn số lần/cửa sổ).
 4. Cảnh báo: chống dội, nhịp báo lại thưa dần, lọc nội dung nhạy cảm.
 5. Cảm biến ghép/độ tin học từ DỮ LIỆU; ba bẫy c2a (không dùng giá trị mới
    nhất cho câu hỏi quá khứ, mẫu âm, bỏ mẫu do_ai=1).
 6. unavailable/unknown KHÔNG được coi là tắt/vắng.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
DIR = GOC / "modules" / "nha-tien-ich"


def _nap_ten(ten: str):
    duong = DIR / f"{ten}.py"
    if not duong.is_file():
        raise ModuleNotFoundError(f"Chưa có {duong}")
    ten_day_du = f"nha_tien_ich_pkg.{ten}"
    if ten_day_du in sys.modules:
        return sys.modules[ten_day_du]
    spec = importlib.util.spec_from_file_location(ten_day_du, duong)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "nha_tien_ich_pkg"
    sys.modules[ten_day_du] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session", autouse=True)
def goi_pkg():
    # Kho dữ liệu lõi KHÔNG nằm dưới /tmp (quy tắc spec) — dựng scratch ngoài
    # /tmp và dọn cuối phiên.
    goc = Path.home() / ".cache" / "web2api-nv195-test"
    goc.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="data_", dir=str(goc)))
    (data / "modules").mkdir(parents=True, exist_ok=True)
    os.environ["WEB2API_DATA"] = str(data)
    # Gói cha giả để các import tương đối (from . import co_che) trong module chạy được.
    for ten in [t for t in sys.modules
                if t == "nha_tien_ich_pkg" or t.startswith("nha_tien_ich_pkg.")]:
        del sys.modules[ten]
    pkg = type(sys)("nha_tien_ich_pkg")
    pkg.__path__ = [str(DIR)]
    sys.modules["nha_tien_ich_pkg"] = pkg
    yield
    # Không xoá data: worker bị ngắt 600s có thể cần debug.


@pytest.fixture(autouse=True)
def moi():
    """Reset trạng thái mọi submodule + HA giả trước mỗi test."""
    co_che = _nap_ten("co_che")
    for ten in ("kich_ban", "canh_bao", "tinh_huong", "cam_bien", "dien"):
        try:
            _nap_ten(ten).dat_lai()
        except Exception:
            pass
    co_che.dat_lai()
    yield
    co_che.dat_lai()


def ha_gia(*thuc_the):
    """Tiêm HA giả: các thực thể {entity_id, state, attributes{device_class}}."""
    co_che = _nap_ten("co_che")

    def doc():
        return [dict(t) for t in thuc_the]
    co_che.dat_doc_trang_thai(doc)


def tt(eid, state, device_class=""):
    return {"entity_id": eid, "state": state,
            "attributes": {"device_class": device_class}}


class DoiTuongGoi:
    """Spy cho co_che.goi_hanh_dong: ghi lại mọi lệnh đã chạy."""

    def __init__(self):
        self.cac_lenh = []

    def __call__(self, entity_id, hanh_dong, tham_so):
        self.cac_lenh.append((entity_id, hanh_dong, tham_so or {}))
        return {"ok": True}


def quan_tri():
    from app import ngu_canh
    return ngu_canh.dat({"id": "qt-test", "loai": "quan_tri", "pham_vi": ["*"]},
                        nguon="nguoi")


def khach():
    from app import ngu_canh
    return ngu_canh.dat({"id": "khach-test", "loai": "khach",
                         "pham_vi": ["thiet_bi:doc"]}, nguon="nguoi")


def khoi_phuc(token):
    from app import ngu_canh
    ngu_canh.khoi_phuc(token)


# ── Hàng rào 1: thử khô mặc định + xác nhận quản trị ─────────────────────────

def test_r1_xem_truoc_khong_chay_gi_ca():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_gia", "off"), tt("lock.khoa_gia", "locked"))
    kich_ban.dinh_nghia("kb1", "Kịch bản giả",
                        [{"thuc_the": "light.den_gia", "hanh_dong": "bat"},
                         {"thuc_the": "lock.khoa_gia", "hanh_dong": "mo_khoa"}])
    kq = kich_ban.xem_truoc("kb1")
    assert kq["ok"] is True and kq["che_do"] == "thu_kho"
    assert len(kq["se_lam"]) == 2
    assert spy.cac_lenh == [], "xem_truoc (thử khô) không được chạy lệnh nào"
    # Bước chạm khoá phải bị đánh dấu chặn ngay ở thử khô.
    buoc_khoa = [b for b in kq["se_lam"] if b["thuc_the"] == "lock.khoa_gia"][0]
    assert buoc_khoa["bi_chan"] is True


def test_r1_chay_thieu_xac_nhan_khong_chay_buoc_nao():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_gia", "off"))
    kich_ban.dinh_nghia("kb2", "KB", [{"thuc_the": "light.den_gia", "hanh_dong": "bat"}])
    tok = quan_tri()
    try:
        kq = kich_ban.chay("kb2")  # không xac_nhan
    finally:
        khoi_phuc(tok)
    assert kq["ok"] is False and kq["loi"] == "can_xac_nhan"
    assert "xem_truoc" in kq, "phải kèm bản thử khô khi từ chối"
    assert spy.cac_lenh == []


def test_r1_chay_co_xac_nhan_nhung_khong_phai_quan_tri():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_gia", "off"))
    kich_ban.dinh_nghia("kb3", "KB", [{"thuc_the": "light.den_gia", "hanh_dong": "bat"}])
    tok = khach()
    try:
        kq = kich_ban.chay("kb3", xac_nhan=True)
    finally:
        khoi_phuc(tok)
    assert kq["ok"] is False and kq["loi"] == "can_xac_nhan"
    assert spy.cac_lenh == []


def test_r1_chay_dung_quan_tri_va_xac_nhan():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_gia", "off"))
    kich_ban.dinh_nghia("kb4", "KB", [{"thuc_the": "light.den_gia", "hanh_dong": "bat"}])
    tok = quan_tri()
    try:
        kq = kich_ban.chay("kb4", xac_nhan=True)
    finally:
        khoi_phuc(tok)
    assert kq["ok"] is True and kq["that_bai"] == 0
    assert spy.cac_lenh == [("light.den_gia", "bat", {})]


def test_r1_nut_tat_hang_rao_toan_bo():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_gia", "off"))
    kich_ban.dinh_nghia("kb5", "KB", [{"thuc_the": "light.den_gia", "hanh_dong": "bat"}])
    # hang_rao_bat=False: chạy ngay không cần xác nhận (chỉ dùng khi có người
    # giám sát trực tiếp) — nhưng hàng rào 2/3/6 vẫn bật (kiểm ở test khác).
    kq = kich_ban.chay("kb5", hang_rao_bat=False)
    assert kq["ok"] is True
    assert spy.cac_lenh == [("light.den_gia", "bat", {})]


# ── Hàng rào 2: không bao giờ tự chạy khoá/bếp/bình theo LOẠI ────────────────

def test_r2_khoa_bi_chan_ngay_ca_khi_da_xac_nhan():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_gia", "off"), tt("lock.khoa_cua_gia", "locked"))
    kich_ban.dinh_nghia("kbk", "Mở khoá tự động",
                        [{"thuc_the": "lock.khoa_cua_gia", "hanh_dong": "mo_khoa"}])
    tok = quan_tri()
    try:
        kq = kich_ban.chay("kbk", xac_nhan=True)
    finally:
        khoi_phuc(tok)
    buoc = kq["ket_qua"][0]
    assert buoc["ok"] is False and buoc["loi"] == "bi_chan_nguy_hiem"
    assert spy.cac_lenh == [], "khoá KHÔNG BAO GIỜ được chạy trong kịch bản"


def test_r2_bep_xac_dinh_theo_device_class_khong_theo_ten():
    hang_rao = _nap_ten("hang_rao")
    # Bếp ngụy trang tên lành nhưng device_class báo bếp → vẫn nguy hiểm.
    assert hang_rao.la_thuc_the_nguy_hiem("switch", "hob") is True
    assert hang_rao.la_thuc_the_nguy_hiem("switch", "oven") is True
    # Công tắc thường → an toàn.
    assert hang_rao.la_thuc_the_nguy_hiem("switch", "") is False
    assert hang_rao.la_thuc_the_nguy_hiem("light", "") is False


def test_r2_loai_moi_chua_biet_fail_closed():
    hang_rao = _nap_ten("hang_rao")
    # Loại mới chưa biết (không trong MIEN_AN_TOAN) ⇒ mặc định nguy hiểm.
    assert hang_rao.la_thuc_the_nguy_hiem("may_cat_co", "") is True
    assert hang_rao.la_thuc_the_nguy_hiem("thiet_bi_la_xyz", "") is True
    # Các miền nguy hiểm đã biết.
    for mien in ("lock", "water_heater", "alarm_control_panel", "valve",
                 "cover", "siren", "vacuum"):
        assert hang_rao.la_thuc_the_nguy_hiem(mien, "") is True, mien


def test_r2_binh_nong_lanh_va_bep_trong_kich_ban_bi_chan():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("water_heater.binh_gia", "off"),
           tt("switch.bep_gia", "off", "stove"))
    kich_ban.dinh_nghia("kbn", "Đun nước",
                        [{"thuc_the": "water_heater.binh_gia", "hanh_dong": "bat"},
                         {"thuc_the": "switch.bep_gia", "hanh_dong": "bat"}])
    tok = quan_tri()
    try:
        kq = kich_ban.chay("kbn", xac_nhan=True)
    finally:
        khoi_phuc(tok)
    assert all(b["loi"] == "bi_chan_nguy_hiem" for b in kq["ket_qua"])
    assert spy.cac_lenh == []


# ── Hàng rào 3: chống lặp kịch bản ───────────────────────────────────────────

def test_r3_kich_ban_vong_that_bi_chan():
    hang_rao = _nap_ten("hang_rao")
    cl = hang_rao.ChongLap()
    assert cl.vao("A", now=1000.0)["ok"] is True
    assert cl.vao("B", now=1000.0)["ok"] is True
    kq = cl.vao("A", now=1000.0)  # A → B → A: vòng lặp thật
    assert kq["ok"] is False and kq["loi"] == "vong_lap"
    assert "A" in kq["chi_tiet"] and "B" in kq["chi_tiet"]


def test_r3_chay_day_trong_cua_so_bi_chan():
    hang_rao = _nap_ten("hang_rao")
    cl = hang_rao.ChongLap()
    for i in range(5):
        assert cl.vao("X", now=1000.0 + i)["ok"] is True
        cl.ra("X")
    kq = cl.vao("X", now=1004.0)
    assert kq["ok"] is False and kq["loi"] == "chay_day"
    # Qua cửa sổ 60s thì chạy lại được.
    assert cl.vao("X", now=1100.0)["ok"] is True


def test_r3_chuoi_qua_sau_bi_chan():
    hang_rao = _nap_ten("hang_rao")
    cl = hang_rao.ChongLap()
    assert cl.vao("A", now=1.0)["ok"] is True
    assert cl.vao("B", now=1.0)["ok"] is True
    assert cl.vao("C", now=1.0)["ok"] is True
    kq = cl.vao("D", now=1.0)
    assert kq["ok"] is False and kq["loi"] == "sau_qua"


# ── Hàng rào 4: chống dội + lọc nhạy cảm ─────────────────────────────────────

def test_r4_trung_trong_cua_so_chong_doi_chi_dem():
    canh_bao = _nap_ten("canh_bao")
    k1 = canh_bao.gui("cam_gia", "mo_cua", "cao", "Cửa mở", now=1000.0)
    assert k1["trang_thai"] == "da_gui"
    k2 = canh_bao.gui("cam_gia", "mo_cua", "cao", "Cửa mở", now=1100.0)
    assert k2["trang_thai"] == "da_gom" and k2["so_lan"] == 2
    k3 = canh_bao.gui("cam_gia", "mo_cua", "cao", "Cửa mở", now=1200.0)
    assert k3["trang_thai"] == "da_gom" and k3["so_lan"] == 3
    assert len(canh_bao.dang_bao()) == 1


def test_r4_nhip_bao_lai_thua_dan_theo_c2a():
    canh_bao = _nap_ten("canh_bao")
    hang_rao = _nap_ten("hang_rao")
    t0 = 1000.0
    assert canh_bao.gui("tb", "loai", "cao", "Nội dung", now=t0)["trang_thai"] == "da_gui"
    # Trong cửa sổ gom 300s: gom.
    assert canh_bao.gui("tb", "loai", "cao", "Nội dung", now=t0 + 200)["trang_thai"] == "da_gom"
    # Hết cửa sổ gom (= nhịp 1: 5p) → báo lại lần 1.
    k = canh_bao.gui("tb", "loai", "cao", "Nội dung", now=t0 + 400)
    assert k["trang_thai"] == "bao_lai" and k["lan_bao_lai"] == 1
    # Nhịp tiếp theo là 30p: gọi ở 10p sau lần 1 → vẫn chờ.
    k = canh_bao.gui("tb", "loai", "cao", "Nội dung", now=t0 + 400 + 600)
    assert k["trang_thai"] == "cho_nhip" and k["bao_lai_sau_giay"] > 0
    # Qua nhịp 30p → báo lại lần 2.
    k = canh_bao.gui("tb", "loai", "cao", "Nội dung", now=t0 + 400 + 1900)
    assert k["trang_thai"] == "bao_lai" and k["lan_bao_lai"] == 2
    assert hang_rao.NHIP_BAO_LAI == (300, 1800, 3600, 21600, 86400)


def test_r4_loc_nhay_cam_che_ten_va_vi_tri():
    hang_rao = _nap_ten("hang_rao")
    s = hang_rao.loc_nhay_cam("anh Việt ở số 12 đường Giả, toạ độ 10.123,106.456 phát hiện chuyển động")
    assert "Việt" not in s
    assert "10.123" not in s and "106.456" not in s
    assert "đường Giả" not in s
    assert "chuyển động" in s  # nội dung chính giữ lại


def test_r4_muc_do_sai_bi_tu_choi():
    canh_bao = _nap_ten("canh_bao")
    kq = canh_bao.gui("tb", "loai", "rat_cao", "x", now=1000.0)
    assert kq["ok"] is False and kq["loi"] == "muc_do_sai"


# ── Hàng rào 5: học từ DỮ LIỆU, ba bẫy c2a ──────────────────────────────────

def test_r5_bay1_khong_dung_gia_tri_moi_nhat_cho_cau_hoi_qua_khu():
    do_tin = _nap_ten("do_tin")
    # Chuỗi: off → on lúc 2000 → off lúc 3000; hiện tại (4000) là off.
    sk = [(1000.0, "off"), (2000.0, "on"), (3000.0, "off")]
    assert do_tin.chuoi_tai_moc(sk, 2500.0) == "on", \
        "tại mốc 2500 cảm biến đang on — không được trả trạng thái mới nhất (off)"
    assert do_tin.chuoi_tai_moc(sk, 3500.0) == "off"
    assert do_tin.chuoi_tai_moc(sk, 500.0) is None


def test_r5_bay3_hoc_bo_mau_do_ai_tu_gay():
    tinh_huong = _nap_ten("tinh_huong")
    # Ghi 3 ngày: mỗi ngày 1 sự kiện thật (do_ai=0) + 1 sự kiện do AI tự gây.
    for ngay in range(3):
        goc = 1_700_000_000.0 + ngay * 86400 + 7 * 3600  # ~07:00 +07
        tinh_huong.ghi("binary_sensor.presence_gia", "on", ts=goc, do_ai=0)
        tinh_huong.ghi("binary_sensor.presence_gia", "on", ts=goc + 60, do_ai=1)
    sk_thuong = tinh_huong.su_kien(1_700_000_000.0 - 1, 1_700_000_000.0 + 3 * 86400,
                                   bo_do_ai=True)
    sk_tat = tinh_huong.su_kien(1_700_000_000.0 - 1, 1_700_000_000.0 + 3 * 86400,
                                bo_do_ai=False)
    assert len(sk_thuong) == 3 and len(sk_tat) == 6
    assert all(not s.get("do_ai") for s in sk_thuong)


def test_r5_bay2_phai_co_mau_am():
    do_tin = _nap_ten("do_tin")
    mau_duong = [{"ts": 1000.0 + i * 100, "thiet_bi": "cb", "gia_tri": "on"}
                 for i in range(4)]
    mau = do_tin.them_mau_am(mau_duong)
    am = [m for m in mau if m.get("nhan_mau") == "am"]
    duong = [m for m in mau if m.get("nhan_mau") != "am"]
    assert len(am) == len(mau_duong), "mỗi mẫu dương sinh một mẫu âm"
    assert len(duong) == 4


def test_r5_do_tin_nhieu_ket_hoc_tu_du_lieu():
    do_tin = _nap_ten("do_tin")
    # Nhiễu: đổi rất dày (mỗi 3s) suốt cửa sổ 0.1 ngày, phần lớn lần ở chớp
    # nhoáng (<10s) → doi_ngay ~28800 ≥ 150, ngan_tl = 1.0 → «nhieu».
    # Port theo test_nhieu_vs_lanh của c2a: dữ liệu PHẢI lấp đầy cửa sổ
    # (so_ngay) và now phải gắn với dữ liệu, không dùng giờ thật.
    now = 1_700_000_000.0
    so_ngay = 0.1
    sk_nhieu = []
    t = now - so_ngay * 86400
    k = 0
    while t < now:
        sk_nhieu.append((t, "on" if k % 2 == 0 else "off"))
        t += 3.0
        k += 1
    kq = do_tin.do_tin("cb_nhieu_gia", sk_nhieu, so_ngay=so_ngay, now=now)
    assert kq.get("nhan") == "nhieu", f"cảm biến đổi dày + chớp nhoáng phải bị đánh dấu nhiễu: {kq}"


def test_r5_nguong_giu_hoc_tu_du_lieu_khong_cung_theo_ten():
    do_tin = _nap_ten("do_tin")
    # Hai cảm biến khác nhau, dữ liệu khác nhau → ngưỡng giữ khác nhau.
    moi_a = [5.0] * 20   # toàn lần ở ngắn
    moi_b = [100.0] * 20  # toàn lần ở dài
    assert do_tin._nguong_giu(moi_a) < do_tin._nguong_giu(moi_b)


# ── Hàng rào 6: unavailable không là tắt/vắng ─────────────────────────────────

def test_r6_unavailable_khong_duoc_coi_la_tat():
    hang_rao = _nap_ten("hang_rao")
    for tt_xau in ("unavailable", "unknown", "UNAVAILABLE", "Unknown", "", "none"):
        assert hang_rao.coi_la_tat(tt_xau) is False, tt_xau
    for tt_tat in ("off", "closed", "locked", "idle", "standby"):
        assert hang_rao.coi_la_tat(tt_tat) is True, tt_tat
    assert hang_rao.coi_la_tat("on") is False


def test_r6_kich_ban_bo_qua_thiet_bi_mat_ket_noi():
    kich_ban = _nap_ten("kich_ban")
    co_che = _nap_ten("co_che")
    spy = DoiTuongGoi()
    co_che.dat_goi_hanh_dong(spy)
    ha_gia(tt("light.den_mat_mang", "unavailable"), tt("light.den_ok", "off"))
    kich_ban.dinh_nghia("kb6", "KB",
                        [{"thuc_the": "light.den_mat_mang", "hanh_dong": "bat"},
                         {"thuc_the": "light.den_ok", "hanh_dong": "bat"}])
    tok = quan_tri()
    try:
        kq = kich_ban.chay("kb6", xac_nhan=True)
    finally:
        khoi_phuc(tok)
    assert kq["ket_qua"][0]["loi"] == "bo_qua_khong_ro"
    assert kq["ket_qua"][1]["ok"] is True
    assert spy.cac_lenh == [("light.den_ok", "bat", {})], \
        "thiết bị unavailable không được kích hoạt hành động"


def test_r6_co_nguoi_tra_khong_ro_chu_khong_vang():
    co_nguoi = _nap_ten("co_nguoi")
    ha_gia(tt("binary_sensor.hien_dien_gia", "unavailable", "presence"))
    kq = co_nguoi.hien_tai()
    assert kq["co_nguoi"] is None, \
        f"cảm biến mất kết nối phải trả «không rõ» (None), không phải «vắng»: {kq}"


# ── Bảo mật: 401/403/IDOR/XSS/SSRF ───────────────────────────────────────────

def test_403_thieu_ngu_canh_loi():
    from app import thiet_bi, ngu_canh
    from app.ngu_canh import NGU_CANH
    tok = NGU_CANH.set(None)
    try:
        with pytest.raises(Exception) as ei:
            asyncio.run(thiet_bi.thuc_hien_hanh_dong_loi(
                "nhatienich.kich_ban", "danh_sach", {}))
        assert getattr(ei.value, "ma", 0) in (401, 403)
    finally:
        ngu_canh.khoi_phuc(tok)


def test_idor_thuc_the_khac_bi_tu_choi():
    mod_ten = _nap_ten("module")
    m = mod_ten.Module({"tien_to": "nhatienich"})
    kq = asyncio.run(m.thuc_hien("home_assistant.den_phong_khach", "bat", {}))
    assert kq["ok"] is False and kq["loi"] == "khong_thay_thuc_the"


def test_xss_ten_kich_ban_bi_lam_sach():
    mod_ten = _nap_ten("module")
    m = mod_ten.Module({"tien_to": "nhatienich"})
    kq = asyncio.run(m.thuc_hien("nhatienich.kich_ban", "dinh_nghia", {
        "id": "xss1", "ten": "<script>alert(1)</script>Đèn",
        "buoc": [{"thuc_the": "light.den_gia", "hanh_dong": "bat"}]}))
    assert kq["ok"] is True
    assert "<script>" not in kq["kich_ban"]["ten"]


def test_ssrf_nut_loopback_bi_chan():
    mod_ten = _nap_ten("module")
    m = mod_ten.Module({"tien_to": "nhatienich"})
    kq = asyncio.run(m.cai({"nut": "ups@127.0.0.1"}))
    assert kq["ok"] is False and "NUT" in (kq.get("loi") or "")
    kq2 = asyncio.run(m.cai({"nut": "ups@example.com"}))
    assert kq2["ok"] is False, "host ngoài mạng nhà phải bị chặn (SSRF)"


def test_ssrf_nut_lan_rieng_tu_duoc_chap_nhan():
    mod_ten = _nap_ten("module")
    m = mod_ten.Module({"tien_to": "nhatienich"})
    kq = asyncio.run(m.cai({"nut": "ups@192.0.2.10"}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["nut"].startswith("ups@192.0.2.10")


def test_cai_tra_cau_hinh_luu_bai_hoc_nv184():
    mod_ten = _nap_ten("module")
    m = mod_ten.Module({"tien_to": "nhatienich"})
    kq = asyncio.run(m.cai({"nut": "", "hang_rao_bat": "0"}))
    assert kq["ok"] is True
    assert kq.get("cau_hinh_luu") == {"nut": "", "hang_rao_bat": "0"}, \
        "cai() phải trả cau_hinh_luu (lỗi NV184: lõi lưu {} → module như chưa cấu hình)"
    assert m._hang_rao_bat is False


# ── Qua lõi thật (bài học NV181) ─────────────────────────────────────────────

def test_qua_so_module_that_thuc_the_va_hanh_dong(tmp_path, monkeypatch):
    """Module vào catalog lõi, cai() ok, lõi đọc 7 thực thể và gọi hành động."""
    import shutil
    from app import so_module
    from app.so_module import SoModule

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    mod_dir = tmp_path / "modules" / "nha-tien-ich"
    shutil.copytree(DIR, mod_dir, ignore=shutil.ignore_patterns("__pycache__"))

    so = SoModule()
    asyncio.run(so.khoi_dong())
    assert "nha-tien-ich" in so.catalog, "module phải có trong catalog lõi"
    kq_cai = asyncio.run(so.cai("nha-tien-ich", {"nut": "", "hang_rao_bat": "1"}))
    assert kq_cai["ok"] is True, f"cai thất bại: {kq_cai}"

    mod = so.dang_chay.get("nha-tien-ich")
    assert mod is not None, "lõi phải nạp được module nha-tien-ich"
    tts = asyncio.run(mod.thuc_the(None))
    ids = {t["id"] for t in tts}
    assert ids == {"nhatienich.kich_ban", "nhatienich.canh_bao",
                   "nhatienich.tinh_huong", "nhatienich.dien",
                   "nhatienich.co_nguoi", "nhatienich.cam_bien",
                   "nhatienich.su_co"}, ids

    # SEC-004f: thực thể mới hiệu lực nguy_hiem tới khi quản trị xác nhận rủi ro.
    from app import thiet_bi as _tb
    for e in tts:
        _tb.xac_nhan_thuc_the(e["id"], e.get("tinh_nang"),
                              rui_ro_mac_dinh=e.get("rui_ro_mac_dinh"))

    # Gọi qua cổng lõi thuc_hien_hanh_dong_loi với ngữ cảnh quản trị.
    tok = quan_tri()
    try:
        ma, kq = asyncio.run(
            __import__("app.thiet_bi", fromlist=["thuc_hien_hanh_dong_loi"])
            .thuc_hien_hanh_dong_loi("nhatienich.kich_ban", "danh_sach", {},
                                     so=so))
    finally:
        khoi_phuc(tok)
    assert ma == 200 and kq["ok"] is True and kq["danh_sach"] == []

    # Định nghĩa + thử khô qua lõi.
    tok = quan_tri()
    try:
        from app import thiet_bi as tb
        ma, kq = asyncio.run(tb.thuc_hien_hanh_dong_loi(
            "nhatienich.kich_ban", "dinh_nghia",
            {"id": "loi1", "ten": "KB qua lõi",
             "buoc": [{"thuc_the": "light.den_gia", "hanh_dong": "bat"}]},
            so=so))
        assert ma == 200 and kq["ok"] is True
        ma, kq = asyncio.run(tb.thuc_hien_hanh_dong_loi(
            "nhatienich.kich_ban", "xem_truoc", {"id": "loi1"}, so=so))
        assert ma == 200 and kq["ok"] is True and kq["che_do"] == "thu_kho"
        # Chạy không xác nhận qua lõi → can_xac_nhan (không 200-ok).
        ma, kq = asyncio.run(tb.thuc_hien_hanh_dong_loi(
            "nhatienich.kich_ban", "chay", {"id": "loi1"}, so=so))
        assert kq["ok"] is False and kq["loi"] == "can_xac_nhan"
    finally:
        khoi_phuc(tok)
    asyncio.run(so.dung_het())


# ── Tương đương c2a (port một phần test c2a, kiểm hành vi đã port) ─────────────

def test_parity_dien_mat_dien_sau_10s_chay_pin():
    dien = _nap_ten("dien")
    # Port test_dien_nha: OB liên tục 10s → báo mất điện (chớp vài giây thì im).
    so: dict = {}
    t0 = 1_000_000.0
    d = {"ups.status": "OB", "battery.charge": "80", "battery.charge.low": "20",
         "ups.load": "30"}
    assert dien.buoc(so, d, t0) == [], "mới chạy pin vài giây: chưa báo"
    tin = dien.buoc(so, d, t0 + 11.0)
    assert len(tin) == 1 and tin[0][0] == dien.KHOA
    assert "Mất điện" in tin[0][1]


def test_parity_dien_co_dien_lai_bao_phuc_hoi():
    dien = _nap_ten("dien")
    so: dict = {}
    t0 = 1_000_000.0
    d_ob = {"ups.status": "OB", "battery.charge": "80", "ups.load": "30"}
    dien.buoc(so, d_ob, t0)
    dien.buoc(so, d_ob, t0 + 11.0)  # đã báo mất
    d_ol = {"ups.status": "OL", "battery.charge": "95", "ups.load": "10"}
    tin = dien.buoc(so, d_ol, t0 + 600.0)
    assert any("Có điện lại" in t for _, t in tin)


def test_parity_dien_mat_doc_180s_bao_loi_he_thong():
    dien = _nap_ten("dien")
    so: dict = {}
    t0 = 1_000_000.0
    assert dien.buoc(so, None, t0) == []
    tin = dien.buoc(so, None, t0 + 181.0)
    assert any(k == dien.KHOA_LOI for k, _ in tin)


def test_parity_canh_bao_doi_tuong_giong_c2a():
    canh_bao = _nap_ten("canh_bao")
    # Port ý test_canh_bao_nha: gửi lần đầu da_gui, trùng trong 300s da_gom.
    t0 = 2_000_000.0
    k1 = canh_bao.gui("tb1", "qua_nhiet", "cao", "Nóng", now=t0)
    assert k1["trang_thai"] == "da_gui"
    k2 = canh_bao.gui("tb1", "qua_nhiet", "cao", "Nóng", now=t0 + 100)
    assert k2["trang_thai"] == "da_gom"
    assert canh_bao.het("tb1", "qua_nhiet") is True
    k3 = canh_bao.gui("tb1", "qua_nhiet", "cao", "Nóng", now=t0 + 200)
    assert k3["trang_thai"] == "da_gui", "hết vấn đề → báo lại từ đầu"


def test_parity_cam_bien_ghep_va_hoac():
    cam_bien = _nap_ten("cam_bien")
    cam_bien.dat("nhatienich.ghep_g1", "Ghép giả", {"va": ["binary_sensor.a_gia", "binary_sensor.b_gia"]})
    tt_map = {"binary_sensor.a_gia": "on", "binary_sensor.b_gia": "on"}
    assert cam_bien.tinh({"va": ["binary_sensor.a_gia", "binary_sensor.b_gia"]}, tt_map) is True
    tt_map["binary_sensor.b_gia"] = "off"
    assert cam_bien.tinh({"va": ["binary_sensor.a_gia", "binary_sensor.b_gia"]}, tt_map) is False
    assert cam_bien.tinh({"hoac": ["binary_sensor.a_gia", "binary_sensor.b_gia"]}, tt_map) is True
    assert cam_bien.tinh({"khong": "binary_sensor.a_gia"}, tt_map) is False


def test_parity_su_co_unavailable_luon_la_mat_ket_noi():
    su_co = _nap_ten("su_co")
    ha_gia(tt("light.den_gia", "unavailable"))
    kq = su_co.chan_doan("light.den_gia")
    assert "mất kết nối" in kq.get("nguyen_nhan", ""), kq


# ── Hợp đồng C1 (bài học NV181: kiểm bằng cấu trúc) ───────────────────────────

def test_hop_dong_c1_thuc_the_async_va_tien_to():
    import inspect
    mod_ten = _nap_ten("module")
    assert inspect.iscoroutinefunction(mod_ten.Module.thuc_the)
    assert inspect.iscoroutinefunction(mod_ten.Module.trang_thai)
    assert inspect.iscoroutinefunction(mod_ten.Module.thuc_hien)
    assert inspect.iscoroutinefunction(mod_ten.Module.cai)
    m = mod_ten.Module({"tien_to": "nhatienich"})
    tts = asyncio.run(m.thuc_the(None))
    assert len(tts) == 7
    for t in tts:
        assert t["id"].startswith("nhatienich."), t["id"]
        assert t.get("rui_ro_mac_dinh") in ("doc", "ghi", "nguy_hiem")
        for tn in t["tinh_nang"]:
            assert tn.get("hanh_dong"), tn


def test_manifest_khai_thiet_bi_va_hang_rao_config():
    import json
    mf = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
    assert "thiet_bi" in mf["cung_cap"]
    assert mf["tien_to"] == "nhatienich"
    khoa = {f["khoa"] for f in mf["truong_cau_hinh"]}
    assert {"nut", "hang_rao_bat"} <= khoa
