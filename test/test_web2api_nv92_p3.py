"""Test NV92 P3: cầu nối lõi + tai_dung_ho_so (chatgpt-web mẫu) + API tái dùng.

- Cầu nối lõi `app.khoi_phuc_phien.lay_trinh_duyet_that`: tìm module theo KHẢ
  NĂNG «trinh_duyet» (không theo tên); không có/chưa cấu hình/lỗi → None
  (đóng cửa, không đoán); hồ sơ sai khuôn → None.
- `tai_dung_ho_so` của chatgpt-web: đi tắt khi hồ sơ đã đăng nhập; bấm Google
  khi chưa; CAPTCHA/2FA/xác minh → can_nguoi (KHÔNG cố vượt); không lấy được
  token → lỗi rõ; không bao giờ điền mật khẩu; bí mật không vào kết quả.
- `POST /api/tai-khoan/tai-dung`: 401/403/404/400/501/503; thành công lưu qua
  `cai()` của đúng module, chỉ trả khoá che; hai module cùng một hồ sơ →
  tài khoản độc lập (lưu riêng từng module).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, khoi_phuc_phien
from app.main import app
from app.tai_khoan_hang_loat import BiMatTam, KetQuaDangNhap

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
HO_SO = "ab" * 16
TOKEN_GIA = "token-tai-dung-gia-001"


# ───────────── A. Cầu nối lõi ─────────────

class _ModKhongLienQuan:
    manifest = {"id": "mod-khac", "cung_cap": ["chat"]}


class _ModTrinhDuyet:
    def __init__(self, ket_qua="DIEU_KHIEN"):
        self.manifest = {"id": "mo-dun-nay-khong-phai-vnc-xa",
                         "cung_cap": ["trinh_duyet"]}
        self._kq = ket_qua
        self.da_goi = []

    def trinh_duyet_that(self, ho_so):
        self.da_goi.append(ho_so)
        if isinstance(self._kq, Exception):
            raise self._kq
        return self._kq


class _DieuKhien(khoi_phuc_phien.BoDieuKhienTrinhDuyet):
    async def mo_trang(self, url): pass
    async def dien(self, bo_chon, gia_tri): pass
    async def bam(self, bo_chon): pass
    async def doc_cookie(self, mien): return {}
    async def tin_hieu(self, quy_tac=None): return set()


def test_cau_noi_tim_theo_kha_nang_khong_theo_ten():
    mod_td = _ModTrinhDuyet(_DieuKhien())
    dang_chay = {"mod-khac": _ModKhongLienQuan(), "trinh-duyet-gia": mod_td}
    td = khoi_phuc_phien.lay_trinh_duyet_that(HO_SO, dang_chay)
    assert isinstance(td, khoi_phuc_phien.BoDieuKhienTrinhDuyet)
    assert mod_td.da_goi == [HO_SO]


def test_cau_noi_khong_co_module():
    assert khoi_phuc_phien.lay_trinh_duyet_that(HO_SO, {}) is None
    assert khoi_phuc_phien.lay_trinh_duyet_that(HO_SO, None) is None


def test_cau_noi_module_loi_dong_cua():
    mod_td = _ModTrinhDuyet(RuntimeError("chua cau hinh"))
    assert khoi_phuc_phien.lay_trinh_duyet_that(HO_SO, {"x": mod_td}) is None


def test_cau_noi_tra_kieu_la_dong_cua():
    mod_td = _ModTrinhDuyet("khong-phai-dieu-khien")
    assert khoi_phuc_phien.lay_trinh_duyet_that(HO_SO, {"x": mod_td}) is None
    assert mod_td.da_goi == [HO_SO]


def test_cau_noi_ho_so_sai_khuon():
    mod_td = _ModTrinhDuyet(_DieuKhien())
    assert khoi_phuc_phien.lay_trinh_duyet_that("../etc/passwd", {"x": mod_td}) is None
    assert khoi_phuc_phien.lay_trinh_duyet_that("", {"x": mod_td}) is None
    assert mod_td.da_goi == []


# ───────────── B. tai_dung_ho_so của chatgpt-web ─────────────

class TrinhDuyetGiaP3:
    """Trình duyệt giả cho P3: tín hiệu theo kịch bản, không ghi giá trị."""

    def __init__(self, tin_hieu=(), cookie=None, sau_bam=None):
        self._tin_hieu = set(tin_hieu)
        self._cookie = dict(cookie or {})
        self._sau_bam = dict(sau_bam or {})
        self.da_mo = []
        self.da_bam = []
        self.da_dien = []

    async def mo_trang(self, url):
        self.da_mo.append(url)

    async def dien(self, bo_chon, gia_tri):
        self.da_dien.append(bo_chon)
        raise AssertionError("tai_dung_ho_so không được điền (đăng nhập bằng Google)")

    async def bam(self, bo_chon):
        self.da_bam.append(bo_chon)
        if bo_chon in self._sau_bam:
            self._tin_hieu = set(self._sau_bam[bo_chon])

    async def doc_cookie(self, mien):
        return dict(self._cookie)

    async def tin_hieu(self, quy_tac=None):
        return set(self._tin_hieu)

    async def dong(self):
        pass


def _tai_chatgpt_web():
    ten = "chatgpt_web_nv92_p3"
    for t in [t for t in list(sys.modules) if t == ten or t.startswith(ten + ".")]:
        del sys.modules[t]
    goi = types.ModuleType(ten)
    goi.__path__ = [str(GOC / "modules" / "chatgpt-web")]
    sys.modules[ten] = goi
    spec = importlib.util.spec_from_file_location(
        f"{ten}.module", GOC / "modules" / "chatgpt-web" / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{ten}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod_cgpt():
    mod = _tai_chatgpt_web()
    manifest = json.loads(
        (GOC / "modules" / "chatgpt-web" / "manifest.json").read_text(encoding="utf-8"))
    m = mod.Module(manifest)

    async def kiem_token_ok(token):
        return {"email": "nguoi@example.com", "goi": "free"} if token == TOKEN_GIA else None

    m._kiem_token = kiem_token_ok
    m._mod_nguon = mod  # module nguồn (để monkeypatch asyncio.sleep của nó)
    return m


def test_tai_dung_di_tat_khi_ho_so_da_dang_nhap(mod_cgpt):
    td = TrinhDuyetGiaP3(tin_hieu={"da_dang_nhap"},
                         cookie={"access_token": TOKEN_GIA})
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA
    # Không bấm gì thêm (đi tắt), không điền mật khẩu.
    assert td.da_bam == [] and td.da_dien == []
    assert td.da_mo == ["https://chatgpt.com/auth/login"]
    # Tài khoản đã vào kho RIÊNG của module.
    assert mod_cgpt.co_tai_khoan(mod_cgpt._khoa_tu_token(TOKEN_GIA))


def test_tai_dung_bam_google_khi_chua_dang_nhap(mod_cgpt):
    td = TrinhDuyetGiaP3(
        tin_hieu=set(),
        cookie={"access_token": TOKEN_GIA},
        sau_bam={mod_cgpt._BO_CHON_GOOGLE: {"da_dang_nhap"}})
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, td))
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA
    assert mod_cgpt._BO_CHON_DANG_NHAP in td.da_bam
    assert mod_cgpt._BO_CHON_GOOGLE in td.da_bam
    assert td.da_dien == []


def test_tai_dung_captcha_thi_can_nguoi(mod_cgpt):
    td = TrinhDuyetGiaP3(tin_hieu={"captcha"}, cookie={"access_token": TOKEN_GIA})
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, td))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"


def test_tai_dung_khong_lay_duoc_token(mod_cgpt):
    td = TrinhDuyetGiaP3(tin_hieu={"da_dang_nhap"}, cookie={})
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, td))
    assert not kq.thanh_cong and kq.ma_loi == "khong_lay_duoc_phien"


def test_tai_dung_token_hong(mod_cgpt):
    async def kiem_token_hong(token):
        return None
    mod_cgpt._kiem_token = kiem_token_hong
    td = TrinhDuyetGiaP3(tin_hieu={"da_dang_nhap"},
                         cookie={"access_token": "token-het-han"})
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, td))
    assert not kq.thanh_cong and kq.ma_loi == "phien_khong_dung_duoc"


def test_tai_dung_khong_co_trinh_duyet(mod_cgpt):
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, None))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"


def test_tai_dung_cho_phien_timeout_nhanh(monkeypatch, mod_cgpt):
    """Không bao giờ đăng nhập được → hết 20 lượt chờ (sleep giả → nhanh)."""
    async def ngu_gia(giay):
        return None
    monkeypatch.setattr(mod_cgpt._mod_nguon.asyncio, "sleep", ngu_gia)
    td = TrinhDuyetGiaP3(tin_hieu=set(), cookie={})
    kq = asyncio.run(mod_cgpt.tai_dung_ho_so(HO_SO, td))
    assert not kq.thanh_cong and kq.ma_loi == "khong_lay_duoc_phien"


# ───────────── C. API POST /api/tai-khoan/tai-dung ─────────────

class SoGia:
    def __init__(self):
        self.dang_chay = {}
        self.cai_goi = []

    def tien_to_cua(self, module_id):
        return "gia" if module_id in self.dang_chay else None

    async def cai(self, id_, cau_hinh):
        self.cai_goi.append((id_, cau_hinh))
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}


class ModProviderGia:
    def __init__(self, ket_qua, co_tai_dung=True):
        self.manifest = {"id": "mod-provider-gia", "cung_cap": ["chat"], "truong_cau_hinh": [{"khoa": "tai_khoan", "kieu": "danh_sach_bi_mat"}]}
        self._kq = ket_qua
        self._co_tai_dung = co_tai_dung

    async def tai_dung_ho_so(self, ho_so, trinh_duyet):
        assert self._co_tai_dung
        return self._kq


class ModTrinhDuyetGiaAPI:
    def __init__(self, dieu_khien):
        self.manifest = {"id": "mod-trinh-duyet-gia", "cung_cap": ["trinh_duyet"]}
        self._dc = dieu_khien

    def trinh_duyet_that(self, ho_so):
        return self._dc


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app, raise_server_exceptions=False) as client:
        so = SoGia()
        monkeypatch.setattr(client.app.state, "so", so)
        yield client, so


def _lap_ho_so_lien_ket():
    ban_ghi = danh_tinh.tao("nv92p3@example.com")
    danh_tinh.cap_nhat_trang_thai(ban_ghi["id"], trang_thai="dang_nhap", ho_so=HO_SO)
    return ban_ghi["id"]


def test_tai_dung_401(api):
    client, _ = api
    r = client.post("/api/tai-khoan/tai-dung",
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 401


def test_tai_dung_403_khoa_khach(api):
    from app import khoa_khach
    client, _ = api
    _, khoa_tho = khoa_khach.tao_khoa(nhan="khach-nv92", pham_vi=["chat"])
    r = client.post("/api/tai-khoan/tai-dung",
                    headers={"Authorization": f"Bearer {khoa_tho}"},
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 403


def test_tai_dung_module_chua_cai_404(api):
    client, _ = api
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "module-khong-co", "ho_so": HO_SO})
    assert r.status_code == 404


def test_tai_dung_ho_so_sai_khuon_400(api):
    client, so = api
    so.dang_chay["mod-provider-gia"] = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": "../x"})
    assert r.status_code == 400


def test_tai_dung_ho_so_chua_lien_ket_404(api):
    client, so = api
    so.dang_chay["mod-provider-gia"] = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 404


def test_tai_dung_module_khong_ho_tro_501(api):
    client, so = api
    _lap_ho_so_lien_ket()
    mod = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"), co_tai_dung=False)
    mod.tai_dung_ho_so = None  # module cũ chưa có hợp đồng mới
    so.dang_chay["mod-provider-gia"] = mod
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 501


def test_tai_dung_chua_co_trinh_duyet_503(api):
    client, so = api
    _lap_ho_so_lien_ket()
    so.dang_chay["mod-provider-gia"] = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 503


def test_tai_dung_thanh_cong_va_che_bi_mat(api):
    client, so = api
    dt_id = _lap_ho_so_lien_ket()
    cookie = "cookie=<redacted>"
    so.dang_chay["mod-provider-gia"] = ModProviderGia(KetQuaDangNhap(True, cookie=cookie))
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(_DieuKhien())
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 200, r.text
    du_lieu = r.json()
    assert du_lieu["thanh_cong"] and not du_lieu["can_nguoi"]
    assert du_lieu["khoa"].startswith("…") and len(du_lieu["khoa"]) == 5
    assert du_lieu["danh_tinh"] == dt_id
    # Lưu qua cai() của ĐÚNG module, thân mang chứng chỉ thô…
    assert so.cai_goi == [("mod-provider-gia", {"tai_khoan": {"them": [cookie]}})]
    # …nhưng phản hồi KHÔNG lộ bí mật.
    assert cookie not in r.text
    # Danh tính đã gắn module provider.
    assert "mod-provider-gia" in danh_tinh.lay(dt_id)["gan"]


def test_tai_dung_can_nguoi(api):
    client, so = api
    _lap_ho_so_lien_ket()
    so.dang_chay["mod-provider-gia"] = ModProviderGia(KetQuaDangNhap(False, ma_loi="can_nguoi"))
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(_DieuKhien())
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 200, r.text
    du_lieu = r.json()
    assert not du_lieu["thanh_cong"] and du_lieu["can_nguoi"]
    assert "url_novnc" in du_lieu and du_lieu["huong_dan"]
    assert so.cai_goi == []


def test_tai_dung_hai_module_doc_lap_tren_mot_ho_so(api):
    """Hai provider cùng tái dùng một hồ sơ → mỗi module lưu tài khoản của
    chính nó (độc lập), danh tính gắn cả hai."""
    client, so = api
    dt_id = _lap_ho_so_lien_ket()
    mod_a = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    mod_b = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    mod_a.manifest = {"id": "mod-a", "cung_cap": ["chat"], "truong_cau_hinh": [{"khoa": "tai_khoan", "kieu": "danh_sach_bi_mat"}]}
    mod_b.manifest = {"id": "mod-b", "cung_cap": ["chat"], "truong_cau_hinh": [{"khoa": "tai_khoan", "kieu": "danh_sach_bi_mat"}]}
    so.dang_chay["mod-a"] = mod_a
    so.dang_chay["mod-b"] = mod_b
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(_DieuKhien())
    for mid in ("mod-a", "mod-b"):
        r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                        json={"module_id": mid, "ho_so": HO_SO})
        assert r.status_code == 200, r.text
    assert ("mod-a", {"tai_khoan": {"them": ["cookie=<redacted>"]}}) in so.cai_goi
    assert ("mod-b", {"tai_khoan": {"them": ["cookie=<redacted>"]}}) in so.cai_goi
    gan = danh_tinh.lay(dt_id)["gan"]
    assert "mod-a" in gan and "mod-b" in gan


# ───────────── E. khoi_phuc dùng cầu nối khi trinh_duyet=None (NV92 P1) ──────

HO_SO_KP = "c1" * 16


class ModProviderKP:
    """Provider giả: tầng (b) ghi lại trinh_duyet nhận được rồi báo thành công."""

    def __init__(self):
        self.manifest = {"id": "mod-kp", "tien_to": "modkp"}
        self.trinh_duyet_da_nhan = "chua-goi"

    def ho_so_trinh_duyet(self, khoa):
        return HO_SO_KP

    def danh_tinh_id_cua(self, khoa):
        # NV94-d: cầu nối trình duyệt thật đi qua danh tính → ho_so hex.
        return getattr(self, "_dt_id", None)

    async def lam_moi_phien(self, khoa):
        return khoi_phuc_phien.KetQuaKhoiPhuc(False, ma_loi="het_han")

    async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
        self.trinh_duyet_da_nhan = trinh_duyet
        return khoi_phuc_phien.KetQuaKhoiPhuc(True)


class ModTrinhDuyetKP:
    manifest = {"id": "td-kp", "cung_cap": ["trinh_duyet"]}

    def __init__(self):
        self.dieu_khien = _DieuKhien()

    def trinh_duyet_that(self, ho_so):
        assert ho_so == HO_SO_KP
        return self.dieu_khien


def _tk_kp():
    return khoi_phuc_phien.TaiKhoanCanKhoiPhuc(
        khoa="tk-kp", mat_khau=BiMatTam("mat-khau-gia-nv92-kp"))


def test_khoi_phuc_dung_cau_noi_khi_khong_truyen_trinh_duyet(tmp_path, monkeypatch):
    """khoi_phuc(..., dang_chay=...) tự lấy bộ điều khiển thật qua cầu nối
    khi trinh_duyet=None; tầng (b) nhận đúng bộ điều khiển đó.

    NV94-d: cầu nối đi tài khoản → danh_tinh_id_cua → bản ghi danh tính
    (ho_so hex), không đoán từ ho_so_trinh_duyet.
    """
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    from app import danh_tinh
    dt = danh_tinh.tao("kp@example.com", "kp")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_KP)
    mod = ModProviderKP()
    mod._dt_id = dt["id"]
    td = ModTrinhDuyetKP()
    kq = asyncio.run(khoi_phuc_phien.khoi_phuc(
        mod, _tk_kp(), dang_chay={"mod-kp": mod, "td-kp": td}))
    assert kq.thanh_cong, kq.ma_loi
    assert mod.trinh_duyet_da_nhan is td.dieu_khien


def test_khoi_phuc_khong_cau_noi_thi_giu_none():
    """Không đưa dang_chay → hành vi cũ: tầng (b) nhận trinh_duyet=None."""
    mod = ModProviderKP()
    kq = asyncio.run(khoi_phuc_phien.khoi_phuc(mod, _tk_kp()))
    assert kq.thanh_cong, kq.ma_loi
    assert mod.trinh_duyet_da_nhan is None


def test_khoi_phuc_cau_noi_that_bai_thi_giu_none():
    """Cầu nối không lấy được (không module trinh_duyet) → đóng cửa nhẹ:
    tầng (b) vẫn chạy với trinh_duyet=None như trước."""
    mod = ModProviderKP()
    kq = asyncio.run(khoi_phuc_phien.khoi_phuc(mod, _tk_kp(), dang_chay={}))
    assert kq.thanh_cong, kq.ma_loi
    assert mod.trinh_duyet_da_nhan is None


# ───────────── F. Giành/nhả quyền điều khiển (NV92 P1: không kẹt «đang bận») ──

class _DieuKhienCoGianh(_DieuKhien):
    """Bộ điều khiển Y NHƯ TrinhDuyetThat: có gianh()/nha() phải gọi đúng thứ tự."""

    def __init__(self):
        self.thu_tu: list[str] = []

    async def gianh(self):
        self.thu_tu.append("gianh")

    async def nha(self):
        self.thu_tu.append("nha")


def test_phien_dieu_khien_gianh_truoc_nha_sau():
    async def chay():
        td = _DieuKhienCoGianh()
        async with khoi_phuc_phien.phien_dieu_khien_that(td) as lay:
            assert lay is td
            td.thu_tu.append("dung")
        return td.thu_tu
    assert asyncio.run(chay()) == ["gianh", "dung", "nha"]


def test_phien_dieu_khien_nha_ca_khi_loi():
    class Loi(RuntimeError):
        pass

    async def chay():
        td = _DieuKhienCoGianh()
        with pytest.raises(Loi):
            async with khoi_phuc_phien.phien_dieu_khien_that(td):
                raise Loi("hong")
        return td.thu_tu
    assert asyncio.run(chay()) == ["gianh", "nha"]


def test_phien_dieu_khien_gia_khong_can_gianh():
    async def chay():
        td = _DieuKhien()
        async with khoi_phuc_phien.phien_dieu_khien_that(td) as lay:
            assert lay is td
    asyncio.run(chay())  # không ném


class ModTrinhDuyetKPCoGianh(ModTrinhDuyetKP):
    def __init__(self):
        self.dieu_khien = _DieuKhienCoGianh()


def test_khoi_phuc_cau_noi_gianh_va_nha_quyen(tmp_path, monkeypatch):
    """Cầu nối trong khoi_phuc: giành trước tầng (b), nhả sau — hồ sơ thật
    không bao giờ kẹt «đang bận»."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    from app import danh_tinh
    dt = danh_tinh.tao("kp2@example.com", "kp2")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_KP)
    mod = ModProviderKP()
    mod._dt_id = dt["id"]
    td = ModTrinhDuyetKPCoGianh()
    kq = asyncio.run(khoi_phuc_phien.khoi_phuc(
        mod, _tk_kp(), dang_chay={"mod-kp": mod, "td-kp": td}))
    assert kq.thanh_cong, kq.ma_loi
    assert td.dieu_khien.thu_tu == ["gianh", "nha"]


def test_tai_dung_gianh_va_nha_quyen(api):
    """POST /api/tai-khoan/tai-dung: giành trước khi gọi module, nhả sau."""
    client, so = api
    _lap_ho_so_lien_ket()
    mod_p = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    mod_p.manifest = {"id": "mod-provider-gia", "cung_cap": ["chat"], "truong_cau_hinh": [{"khoa": "tai_khoan", "kieu": "danh_sach_bi_mat"}]}
    td = _DieuKhienCoGianh()
    so.dang_chay["mod-provider-gia"] = mod_p
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(td)
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"]
    assert td.thu_tu == ["gianh", "nha"]


class _DieuKhienBan(_DieuKhien):
    """gianh() lỗi 409 như dịch vụ thật khi hồ sơ đang bận (có người/máy khác)."""

    async def gianh(self):
        e = RuntimeError("Ho so dang ban")
        e.ma = 409
        raise e

    async def nha(self):
        pass


def test_tai_dung_ho_so_dang_ban_409(api):
    """Giành quyền lỗi 409 → API trả 409 ho_so_dang_ban (không 502 oan)."""
    client, so = api
    _lap_ho_so_lien_ket()
    mod_p = ModProviderGia(KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    mod_p.manifest = {"id": "mod-provider-gia", "cung_cap": ["chat"], "truong_cau_hinh": [{"khoa": "tai_khoan", "kieu": "danh_sach_bi_mat"}]}
    so.dang_chay["mod-provider-gia"] = mod_p
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(_DieuKhienBan())
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "ho_so_dang_ban"


def test_tai_dung_dung_ten_truong_theo_manifest(api):
    """Tên trường bí mật do manifest module quyết (module dùng «cookies» chứ
    không phải «tai_khoan»): lõi không được ghi cứng tên trường."""
    client, so = api
    _lap_ho_so_lien_ket()
    cookie = "cookie=<redacted>"
    mod = ModProviderGia(KetQuaDangNhap(True, cookie=cookie))
    mod.manifest["truong_cau_hinh"] = [{"khoa": "cookies", "kieu": "danh_sach_bi_mat"}]
    so.dang_chay["mod-provider-gia"] = mod
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(_DieuKhien())
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 200, r.text
    assert so.cai_goi == [("mod-provider-gia", {"cookies": {"them": [cookie]}})]


def test_tai_dung_luu_that_bai_khong_bao_thanh_cong(api):
    """Module từ chối lưu (cai trả ok False) → không báo thành công, không gắn
    danh tính vào module."""
    client, so = api
    dt_id = _lap_ho_so_lien_ket()
    so.dang_chay["mod-provider-gia"] = ModProviderGia(
        KetQuaDangNhap(True, cookie="cookie=<redacted>"))
    so.dang_chay["mod-trinh-duyet-gia"] = ModTrinhDuyetGiaAPI(_DieuKhien())

    async def tu_choi(id_, cau_hinh):
        return {"ok": False, "ket_qua_tung_khoa": [], "loi": "khoa sai"}
    so.cai = tu_choi
    r = client.post("/api/tai-khoan/tai-dung", headers=QT,
                    json={"module_id": "mod-provider-gia", "ho_so": HO_SO})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is False
    assert "mod-provider-gia" not in danh_tinh.lay(dt_id)["gan"]
