"""NV155c — «nhận workspace làm tài khoản»: POST /api/danh-tinh/nhan-ho-so.

- API (chỉ quản trị): 401 không xác thực; 403 khoá khách/vai thấp; 400 khuôn sai;
  404 hồ sơ không có ở dịch vụ trình duyệt; 409 hồ sơ đang mở (giữ nguyên cơ chế
  SEC «đóng · giữ phiên»); 409 IDOR — hồ sơ đã gắn danh tính khác, email đã gắn
  hồ sơ khác; gắn vào danh tính có sẵn / tạo mới; trạng thái chỉ «dang_nhap»
  khi TÍN HIỆU phiên được kiểm bằng QUY_TAC_TIN_HIEU của module có khả năng
  «dang_nhap_danh_tinh» (không kiểm được → chua_dang_nhap); nhật ký người thực
  hiện; lỗi không lộ email đầy đủ.
- Đỏ trước: đường API chưa có → 404 cho mọi test API.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, khoa_khach, khoi_phuc_phien, nhat_ky
from app.main import app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

HO_SO_1 = "aa" * 16
HO_SO_2 = "bb" * 16
HO_SO_3 = "cc" * 16
HO_SO_MO = "dd" * 16

THU_MUC_HSG = GOC / "modules" / "ho-so-google"
MANIFEST_HSG = json.loads((THU_MUC_HSG / "manifest.json").read_text(encoding="utf-8"))


def _tai_module_hsg():
    ten = "ho_so_google_nv155c_test"
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


mod_hsg = _tai_module_hsg()
ModuleHSG = mod_hsg.Module


class _DieuKhienGia(khoi_phuc_phien.BoDieuKhienTrinhDuyet):
    """Bộ điều khiển trình duyệt giả: đánh giá quy tắc tín hiệu cấu trúc theo cookie
    (giống cơ chế dịch vụ thật: server.doc_tin_hieu)."""

    def __init__(self, cookie: dict):
        self.cookie = dict(cookie)

    async def tin_hieu(self, quy_tac=None) -> set:
        ra = set()
        for q in (quy_tac or []):
            if not isinstance(q, dict):
                continue
            if q.get("loai") == "cookie" and q.get("ten") in self.cookie:
                ra.add(q.get("tin_hieu"))
        return ra

    async def dong(self) -> None:
        pass

    async def mo_trang(self, url: str) -> None:
        pass

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        pass

    async def bam(self, bo_chon: str) -> None:
        pass

    async def doc_cookie(self, mien: str) -> dict:
        return dict(self.cookie)


class _ModTrinhDuyetGia:
    """Module trình duyệt giả đúng hợp đồng: cung_cap «trinh_duyet» + trinh_duyet_ds
    + trinh_duyet_that (có/không cookie đăng nhập Google theo ho_so)."""

    def __init__(self):
        self.manifest = {"id": "trinh-duyet-gia", "cung_cap": ["trinh_duyet"]}
        self.ds: list[dict] = []
        self.cookie: dict[str, dict] = {}
        self.loi_ds: Exception | None = None
        self.loi_tin_hieu: dict[str, Exception] = {}

    async def trinh_duyet_ds(self) -> list[dict]:
        if self.loi_ds is not None:
            raise self.loi_ds
        return [dict(w) for w in self.ds]

    def trinh_duyet_that(self, ho_so):
        dk = _DieuKhienGia(self.cookie.get(ho_so, {}))
        loi = self.loi_tin_hieu.get(ho_so)
        if loi is not None:
            async def tin_hieu_loi(quy_tac=None):
                raise loi
            dk.tin_hieu = tin_hieu_loi
        return dk


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


def _nap(so, cookie_theo_ho_so=None, ws=None, mod_td=None):
    mod_dn = ModuleHSG(MANIFEST_HSG)
    so.dang_chay["ho-so-google"] = mod_dn
    td = mod_td or _ModTrinhDuyetGia()
    if ws is not None:
        td.ds = list(ws)
    if cookie_theo_ho_so:
        td.cookie.update(cookie_theo_ho_so)
    so.dang_chay["trinh-duyet-gia"] = td
    return mod_dn, td


def _ws(ho_so, dang_mo=False, ten="Workspace"):
    return {"ho_so": ho_so, "ten": ten, "loai": "firefox",
            "trang_thai": "dang_mo" if dang_mo else "dong",
            "dang_mo": dang_mo, "thu_cong": dang_mo, "luc_mo": None}


def test_chua_co_duong_401(api):
    client, so = api
    assert client.post("/api/danh-tinh/nhan-ho-so",
                       json={"ho_so": HO_SO_1, "email": "a@example.com"}).status_code == 401


def test_khoa_khach_403(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)])
    _, khoa_tho = khoa_khach.tao_khoa(nhan="khach-155c", pham_vi=["chat"])
    r = client.post("/api/danh-tinh/nhan-ho-so",
                    headers={"Authorization": f"Bearer {khoa_tho}"},
                    json={"ho_so": HO_SO_1, "email": "a@example.com"})
    assert r.status_code == 403


def test_ho_so_sai_khuon_400(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)])
    for xau in ["../x", "ABC", "a" * 15, "zz" * 16, "", 123]:
        r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                        json={"ho_so": xau, "email": "a@example.com"})
        assert r.status_code == 400, xau


def test_email_sai_khuon_400(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)])
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "khong-phai-email"})
    assert r.status_code == 400
    assert "invalid_email" in r.text


def test_503_khi_thieu_trinh_duyet(api):
    client, so = api
    so.dang_chay["ho-so-google"] = ModuleHSG(MANIFEST_HSG)
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "a@example.com"})
    assert r.status_code == 503


def test_404_ho_so_khong_co_o_dich_vu(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_2)])
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "a@example.com"})
    assert r.status_code == 404
    assert "ho_so_khong_co" in r.text

def test_409_ho_so_dang_mo(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_MO, dang_mo=True)])
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_MO, "email": "a@example.com"})
    assert r.status_code == 409
    assert "ho_so_dang_mo" in r.text
    assert danh_tinh.tim_theo_ho_so(HO_SO_MO) is None


def test_201_tao_moi_dang_nhap(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)], cookie_theo_ho_so={HO_SO_1: {"SID": "gia-1"}})
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "Moi@Example.com", "nhan": "cá nhân"})
    assert r.status_code == 201, r.text
    du = r.json()
    assert du["danh_tinh"]["email"] == "moi@example.com"
    assert du["danh_tinh"]["ho_so_trinh_duyet"] == HO_SO_1
    assert du["danh_tinh"]["nhan"] == "cá nhân"
    assert du["trang_thai_dang_nhap"] == "dang_nhap"
    assert du["danh_tinh"]["trang_thai_dang_nhap"] == "dang_nhap"
    assert du["da_co_san"] is False


def test_201_chua_dang_nhap_khi_khong_tin_hieu(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)])
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "moi2@example.com"})
    assert r.status_code == 201, r.text
    assert r.json()["trang_thai_dang_nhap"] == "chua_dang_nhap"


def test_201_chua_dang_nhap_khi_dich_vu_loi_tin_hieu(api):
    """Lỗi khi đọc tín hiệu phải đóng cửa về chua_dang_nhap, không vỡ đường."""
    client, so = api
    _, td = _nap(so, ws=[_ws(HO_SO_1)])
    td.loi_tin_hieu[HO_SO_1] = RuntimeError("dich vu hong")
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "moi3@example.com"})
    assert r.status_code == 201, r.text
    assert r.json()["trang_thai_dang_nhap"] == "chua_dang_nhap"


def test_200_gan_vao_danh_tinh_co_san(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_2)], cookie_theo_ho_so={HO_SO_2: {"__Secure-1PSID": "gia-2"}})
    dt = danh_tinh.tao("co-san@example.com", "nhãn cũ")
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_2, "email": "co-san@example.com"})
    assert r.status_code == 200, r.text
    du = r.json()
    assert du["da_co_san"] is True
    assert du["danh_tinh"]["id"] == dt["id"]
    assert du["danh_tinh"]["ho_so_trinh_duyet"] == HO_SO_2
    assert du["danh_tinh"]["nhan"] == "nhãn cũ"
    assert du["trang_thai_dang_nhap"] == "dang_nhap"


def test_409_idor_ho_so_da_gan_danh_tinh_khac(api):
    """Hồ sơ đã gắn danh tính KHÁC → 409, không được cướp."""
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)], cookie_theo_ho_so={HO_SO_1: {"SID": "gia"}})
    dt_a = danh_tinh.tao("a@example.com")
    danh_tinh.cap_nhat_trang_thai(dt_a["id"], ho_so=HO_SO_1)
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "b@example.com"})
    assert r.status_code == 409
    assert "ho_so_da_gan" in r.text
    assert danh_tinh.tim_theo_ho_so(HO_SO_1)["id"] == dt_a["id"]
    assert danh_tinh.tim_theo_email("b@example.com") is None


def test_409_email_da_gan_ho_so_khac(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1), _ws(HO_SO_2)])
    dt = danh_tinh.tao("c@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_1)
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_2, "email": "c@example.com"})
    assert r.status_code == 409
    assert "email_da_gan_ho_so_khac" in r.text
    assert danh_tinh.tim_theo_email("c@example.com")["ho_so_trinh_duyet"] == HO_SO_1


def test_luy_dang_nhan_lai_cung_email_va_ho_so(api):
    """Nhận lại cùng email + cùng hồ sơ → 200, không tạo bản ghi đôi."""
    client, so = api
    _nap(so, ws=[_ws(HO_SO_3)], cookie_theo_ho_so={HO_SO_3: {"SID": "gia"}})
    r1 = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                     json={"ho_so": HO_SO_3, "email": "lap@example.com"})
    assert r1.status_code == 201
    r2 = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                     json={"ho_so": HO_SO_3, "email": "lap@example.com"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["danh_tinh"]["id"] == r1.json()["danh_tinh"]["id"]
    assert len(danh_tinh.danh_sach()) == 1


def test_nhat_ky_ghi_nguoi_thuc_hien(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1)])
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "nk@example.com"})
    assert r.status_code == 201
    dong = [d for d in nhat_ky.doc(kenh="quan_tri", gioi_han=100)
            if "nhan-ho-so" in str(d.get("tom_tat", ""))]
    assert dong, "phải có dòng nhật ký nhận workspace"
    them = dong[-1].get("them", {})
    assert them.get("id_danh_tinh") == r.json()["danh_tinh"]["id"]
    assert "quan_tri" in str(them.get("nguoi_thuc_hien", ""))


def test_loi_khong_lo_email_day_du(api):
    client, so = api
    _nap(so, ws=[_ws(HO_SO_1), _ws(HO_SO_2)])
    dt = danh_tinh.tao("bi-mat-day-du@example.com")
    danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so=HO_SO_1)
    r1 = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                     json={"ho_so": HO_SO_2, "email": "bi-mat-day-du@example.com"})
    assert r1.status_code == 409
    assert "bi-mat-day-du@example.com" not in r1.text
    r2 = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                     json={"ho_so": HO_SO_1, "email": "khong-phai-email"})
    assert r2.status_code == 400
    assert "khong-phai-email" not in r2.text


def test_502_khi_dich_vu_trinh_duyet_loi(api):
    client, so = api
    _, td = _nap(so, ws=[_ws(HO_SO_1)])
    td.loi_ds = RuntimeError("dich vu khong tra loi")
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": HO_SO_1, "email": "loi@example.com"})
    assert r.status_code == 502
    assert danh_tinh.tim_theo_ho_so(HO_SO_1) is None
