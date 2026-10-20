"""NV155c-SUA — vá A1–A4 của soát độc lập (soat-doc-lap-B.md mục 2) cho
«nhận workspace làm tài khoản» POST /api/danh-tinh/nhan-ho-so.

Khác bộ test NV155c gốc ở bộ điều khiển giả: ở đây mô phỏng ĐÚNG hành vi của
dịch vụ trình duyệt THẬT (dich-vu/trinh-duyet/server.py):

- `tin_hieu()` trên hồ sơ ĐÃ ĐÓNG → dịch vụ trả 404 (`_lay_dieu_khien`);
  hồ sơ mở nhưng CHƯA `gianh()` → 409. (Bản giả NV155c gốc trả tín hiệu tuỳ ý
  nên không bắt được A1: «13/13 test, 0 ca thật».)
- `gianh()` TỰ MỞ hồ sơ Firefox đã đóng trên đĩa (NV94-a) rồi mới cho đọc
  tín hiệu trang sống.
- Kho cookie của hồ sơ đã đóng đọc được QUA API hồ sơ (GET /ho-so/{id}/cookie,
  đọc cookies.sqlite trên đĩa — KHÔNG mở trình duyệt); hồ sơ đang mở → 409,
  Chromium → 501: cả hai đều «không kiểm được» (None).

A1: nhận hồ sơ đã đóng có cookie phiên trong kho → «dang_nhap» (kiểm từ kho
    cookie); không đọc được kho → dự phòng mở tạm có kiểm soát (gianh→nha);
    cả hai đều hỏng → giữ «chua_dang_nhap» (đóng cửa).
A2: nhận lại hồ sơ đã kiểm thật chỉ NÂNG trạng thái, không HẠ vì lần kiểm sau
    yếu hơn/không kiểm được.
A3: xoá danh tính «nhận» chỉ gỡ gắn — đóng hồ sơ GIỮ PHIÊN (giu_phien=True),
    không xoá thư mục workspace của người dùng; danh tính thường vẫn như cũ.
A4: test chính thức vai thấp (phiên web `user` + CSRF) → 403.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, khoa_khach, khoi_phuc_phien
from app.main import app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

HO_SO_1 = "aa" * 16
HO_SO_2 = "bb" * 16

THU_MUC_HSG = GOC / "modules" / "ho-so-google"
MANIFEST_HSG = json.loads((THU_MUC_HSG / "manifest.json").read_text(encoding="utf-8"))


def _tai_module_hsg():
    ten = "ho_so_google_nv155c_sua_test"
    for t in [x for x in list(sys.modules) if x == ten or x.startswith(ten + ".")]:
        del sys.modules[t]
    goi = types.ModuleType(ten)
    goi.__path__ = [str(THU_MUC_HSG)]
    sys.modules[ten] = goi
    spec = importlib.util.spec_from_file_location(f"{ten}.module", THU_MUC_HSG / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{ten}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


ModuleHSG = _tai_module_hsg().Module


class LoiDichVuGia(Exception):
    """Mô phỏng LoiDichVu của module vnc-xa: chỉ mang mã trạng thái."""

    def __init__(self, ma: int):
        super().__init__(f"Dịch vụ trình duyệt trả mã lỗi {ma}.")
        self.ma = ma


class _DieuKhienThatGia(khoi_phuc_phien.BoDieuKhienTrinhDuyet):
    """Bộ điều khiển giả mô phỏng ĐÚNG dịch vụ trình duyệt thật.

    - `kho_cookie`: {mien: {ten: gia_tri}} — kho cookie trên ĐĨA của hồ sơ.
    - `cookie_song`: {ten: gia_tri} — cookie của trang sống (chỉ đọc được sau
      khi hồ sơ MỞ và đã giành quyền).
    - `kho_loi`: đọc kho cookie của hồ sơ đã đóng trả «không kiểm được» (None)
      — mô phỏng 409/501/lỗi dịch vụ ở đường đó.
    - `gianh_loi`: giành quyền cũng hỏng (dịch vụ chết hẳn).
    """

    def __init__(self, kho_cookie=None, cookie_song=None, kho_loi=False,
                 gianh_loi=False):
        self.kho_cookie = {m: dict(c) for m, c in (kho_cookie or {}).items()}
        self.cookie_song = dict(cookie_song or {})
        self.kho_loi = kho_loi
        self.gianh_loi = gianh_loi
        self.dang_mo = False
        self.da_gianh = False

    # ── hành vi dịch vụ thật ──

    async def gianh(self) -> None:
        if self.gianh_loi:
            raise LoiDichVuGia(502)
        # NV94-a: hồ sơ Firefox trên đĩa chưa mở → dịch vụ TỰ MỞ rồi giành.
        self.dang_mo = True
        self.da_gianh = True

    async def nha(self) -> None:
        self.da_gianh = False

    async def tin_hieu(self, quy_tac=None) -> set:
        if not self.dang_mo:
            raise LoiDichVuGia(404)  # _lay_dieu_khien: chưa mở → 404
        if not self.da_gianh:
            raise LoiDichVuGia(409)  # chưa giành quyền → 409
        ra = set()
        for q in (quy_tac or []):
            if not isinstance(q, dict):
                continue
            if q.get("loai") == "cookie" and q.get("ten") in self.cookie_song:
                ra.add(q.get("tin_hieu"))
        return ra

    async def doc_cookie_ho_so_da_dong(self, mien: str):
        # Đường GET /ho-so/{id}/cookie của dịch vụ thật: chỉ chạy khi hồ sơ
        # ĐÃ ĐÓNG (đang mở → 409 → module trả None «không kiểm được»).
        if self.kho_loi or self.dang_mo:
            return None
        return dict(self.kho_cookie.get(mien, {}))

    # ── phần còn lại của hợp đồng ──

    async def mo_trang(self, url: str) -> None:
        pass

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        pass

    async def bam(self, bo_chon: str) -> None:
        pass

    async def doc_cookie(self, mien: str) -> dict:
        return dict(self.cookie_song)

    async def dong(self) -> None:
        self.da_gianh = False


class _ModTrinhDuyetThatGia:
    """Module trình duyệt giả: cung_cap «trinh_duyet» + ds + that + dong."""

    def __init__(self):
        self.manifest = {"id": "trinh-duyet-that-gia", "cung_cap": ["trinh_duyet"]}
        self.ds: list[dict] = []
        self.bo_dieu_khien: dict[str, _DieuKhienThatGia] = {}
        self.da_dong: list[tuple] = []

    async def trinh_duyet_ds(self) -> list[dict]:
        return [dict(w) for w in self.ds]

    def trinh_duyet_that(self, ho_so):
        return self.bo_dieu_khien[ho_so]

    async def trinh_duyet_dong(self, ho_so, giu_phien):
        self.da_dong.append((ho_so, giu_phien))


class _SoGia:
    def __init__(self):
        self.dang_chay = {}

    def tien_to_cua(self, module_id):
        return "gia" if module_id in self.dang_chay else None


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    khoa_khach.lam_moi_cache()
    with TestClient(app, raise_server_exceptions=False) as client:
        so = _SoGia()
        monkeypatch.setattr(client.app.state, "so", so)
        yield client, so


def _ws(ho_so, dang_mo=False):
    return {"ho_so": ho_so, "ten": "Workspace", "loai": "firefox",
            "trang_thai": "dang_mo" if dang_mo else "dong",
            "dang_mo": dang_mo, "thu_cong": dang_mo, "luc_mo": None}


def _nap(so, dieu_khien: _DieuKhienThatGia, ho_so=HO_SO_1):
    so.dang_chay["ho-so-google"] = ModuleHSG(MANIFEST_HSG)
    td = _ModTrinhDuyetThatGia()
    td.ds = [_ws(ho_so)]
    td.bo_dieu_khien[ho_so] = dieu_khien
    so.dang_chay["trinh-duyet-that-gia"] = td
    return td


def _nhan(client, ho_so=HO_SO_1, email="sua155c@example.com"):
    return client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                       json={"ho_so": ho_so, "email": email})


# ── A1 ─────────────────────────────────────────────────────────────────────

def test_a1_ho_so_da_dong_co_cookie_phien_trong_kho_bao_dang_nhap(api):
    """Dịch vụ thật: hồ sơ ĐÃ ĐÓNG → tin_hieu() 404; nhưng kho cookie trên đĩa
    có SID của google.com → phải kiểm được «dang_nhap» từ kho cookie."""
    client, so = api
    dk = _DieuKhienThatGia(kho_cookie={"google.com": {"SID": "gia-tri"}})
    _nap(so, dk)
    r = _nhan(client)
    assert r.status_code == 201, r.text
    du = r.json()
    assert du["trang_thai_dang_nhap"] == "dang_nhap"
    assert du["danh_tinh"]["trang_thai_dang_nhap"] == "dang_nhap"
    assert du["kiem_tin_hieu"] == "cookie_ho_so"
    # Kiểm từ kho KHÔNG mở trình duyệt: hồ sơ vẫn đóng sau khi nhận.
    assert dk.dang_mo is False


def test_a1_ho_so_da_dong_kho_khong_co_cookie_bao_chua_dang_nhap(api):
    client, so = api
    dk = _DieuKhienThatGia(kho_cookie={"google.com": {"NID": "khong-phai-phien"}})
    _nap(so, dk)
    r = _nhan(client)
    assert r.status_code == 201, r.text
    assert r.json()["trang_thai_dang_nhap"] == "chua_dang_nhap"


def test_a1_cookie_mien_khac_khong_tinh(api):
    """Cookie SID nhưng ở miền khác miền quy tắc khai → không được tính."""
    client, so = api
    dk = _DieuKhienThatGia(kho_cookie={"vi-du-khac.example": {"SID": "gia-tri"}})
    _nap(so, dk)
    r = _nhan(client)
    assert r.status_code == 201, r.text
    assert r.json()["trang_thai_dang_nhap"] == "chua_dang_nhap"


def test_a1_du_phong_mo_tam_co_kiem_soat_khi_khong_doc_duoc_kho(api):
    """Không đọc được kho cookie (None) → mở tạm có kiểm soát: giành quyền
    (dịch vụ tự mở) → đọc tín hiệu trang sống → NHẢ quyền sau khi kiểm."""
    client, so = api
    dk = _DieuKhienThatGia(kho_loi=True, cookie_song={"SID": "gia-tri"})
    _nap(so, dk)
    r = _nhan(client)
    assert r.status_code == 201, r.text
    du = r.json()
    assert du["trang_thai_dang_nhap"] == "dang_nhap"
    assert du["kiem_tin_hieu"] == "phien_truc_tiep"
    assert dk.da_gianh is False  # đã nhả quyền sau khi kiểm


def test_a1_khong_kiem_duoc_theo_ca_hai_duong_giu_chua_dang_nhap(api):
    """Kho không đọc được + giành quyền cũng hỏng → đóng cửa chua_dang_nhap,
    đường vẫn trả 201 (không vỡ), ghi rõ không kiểm được."""
    client, so = api
    dk = _DieuKhienThatGia(kho_loi=True, gianh_loi=True,
                           cookie_song={"SID": "gia-tri"})
    _nap(so, dk)
    r = _nhan(client)
    assert r.status_code == 201, r.text
    du = r.json()
    assert du["trang_thai_dang_nhap"] == "chua_dang_nhap"
    assert du["kiem_tin_hieu"] == "khong_kiem_duoc"


# ── A2 ─────────────────────────────────────────────────────────────────────

def _danh_tinh_da_dang_nhap(email="giu@example.com", ho_so=HO_SO_1):
    dt = danh_tinh.tao(email)
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=ho_so,
                                  trang_thai="dang_nhap")
    return dt


def test_a2_nhan_lai_khong_ha_trang_thai_da_kiem_that(api):
    """Danh tính đã «dang_nhap» (luồng /dang-nhap kiểm thật); nhận lại khi lần
    này KHÔNG kiểm được tín hiệu → trạng thái KHÔNG bị hạ."""
    client, so = api
    dk = _DieuKhienThatGia()  # không cookie ở đâu cả → không kiểm được gì
    _nap(so, dk)
    dt = _danh_tinh_da_dang_nhap()
    r = _nhan(client, email="giu@example.com")
    assert r.status_code == 200, r.text
    du = r.json()
    assert du["trang_thai_dang_nhap"] == "dang_nhap"
    assert danh_tinh.lay(dt["id"])["trang_thai_dang_nhap"] == "dang_nhap"


def test_a2_nhan_lai_nang_trang_thai_khi_lan_nay_kiem_duoc(api):
    """Ngược lại: đang «chua_dang_nhap», lần nhận lại kiểm được → NÂNG lên."""
    client, so = api
    dk = _DieuKhienThatGia(kho_cookie={"google.com": {"__Secure-1PSID": "x"}})
    _nap(so, dk)
    dt = danh_tinh.tao("nang@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_1)
    r = _nhan(client, email="nang@example.com")
    assert r.status_code == 200, r.text
    assert r.json()["trang_thai_dang_nhap"] == "dang_nhap"
    assert danh_tinh.lay(dt["id"])["trang_thai_dang_nhap"] == "dang_nhap"


# ── A3 ─────────────────────────────────────────────────────────────────────

def test_a3_xoa_danh_tinh_nhan_chi_go_gan_giu_phien(api):
    """Danh tính do «nhận» gắn: xoá → đóng hồ sơ GIỮ PHIÊN (không xoá thư mục
    workspace có sẵn của người dùng)."""
    client, so = api
    dk = _DieuKhienThatGia()
    td = _nap(so, dk)
    r = _nhan(client, email="nhan@example.com")
    assert r.status_code == 201, r.text
    dt_id = r.json()["danh_tinh"]["id"]
    assert r.json()["danh_tinh"]["ho_so_do_nhan"] is True
    r2 = client.delete(f"/api/danh-tinh/{dt_id}", headers=QT)
    assert r2.status_code == 200, r2.text
    assert td.da_dong == [(HO_SO_1, True)]
    assert danh_tinh.lay(dt_id) is None


def test_a3_xoa_danh_tinh_thuong_van_dong_xoa_han_nhu_cu(api):
    """Danh tính KHÔNG qua đường «nhận» (luồng /dang-nhap tạo hồ sơ): giữ
    hành vi cũ — đóng hồ sơ KHÔNG giữ phiên."""
    client, so = api
    dk = _DieuKhienThatGia()
    td = _nap(so, dk)
    dt = danh_tinh.tao("thuong@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_1)
    r = client.delete(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 200, r.text
    assert td.da_dong == [(HO_SO_1, False)]


# ── A4 ─────────────────────────────────────────────────────────────────────

def test_a4_vai_thap_phien_web_403(api):
    """Luật cứng: vai thấp (phiên web `user`, có CSRF đúng) gọi nhan-ho-so
    → 403 và hồ sơ KHÔNG bị gắn."""
    client, so = api
    dk = _DieuKhienThatGia(kho_cookie={"google.com": {"SID": "x"}})
    _nap(so, dk)
    r = client.post("/api/nguoi-dung", headers=QT,
                    json={"ten_dang_nhap": "nv155cuser", "mat_khau": "MatKhauManh123!",
                          "vai": "user", "pham_vi_toi_da": ["chat"]})
    assert r.status_code == 200, r.text[:200]
    r = client.post("/api/dang-nhap",
                    json={"ten_dang_nhap": "nv155cuser", "mat_khau": "MatKhauManh123!"})
    assert r.status_code == 200, r.text[:200]
    r = client.get("/api/toi")
    assert r.status_code == 200, r.text[:200]
    csrf = {"X-CSRF-Token": r.json()["csrf"]}
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=csrf,
                    json={"ho_so": HO_SO_1, "email": "thap@example.com"})
    assert r.status_code == 403, r.text[:200]
    assert danh_tinh.tim_theo_ho_so(HO_SO_1) is None
    # Không CSRF cũng không lọt.
    r = client.post("/api/danh-tinh/nhan-ho-so",
                    json={"ho_so": HO_SO_1, "email": "thap@example.com"})
    assert r.status_code in (401, 403)
    assert danh_tinh.tim_theo_ho_so(HO_SO_1) is None


def test_a4_khoa_khach_403_giu_nguyen(api):
    """Đối chứng: khoá khách (đã có ở bộ cũ) vẫn 403 sau khi sửa."""
    client, so = api
    _nap(so, _DieuKhienThatGia())
    _, khoa_tho = khoa_khach.tao_khoa(nhan="khach-sua", pham_vi=["chat"])
    r = client.post("/api/danh-tinh/nhan-ho-so",
                    headers={"Authorization": f"Bearer {khoa_tho}"},
                    json={"ho_so": HO_SO_1, "email": "a@example.com"})
    assert r.status_code == 403
