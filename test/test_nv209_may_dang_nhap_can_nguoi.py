"""Test NV209 — module may-dang-nhap ánh xạ trạng thái need_human của dịch vụ
sang can_nguoi=True kèm hướng dẫn tiếng Việt; lỗi vĩnh viễn (bị khoá, bị từ
chối) không thử lại.

Đỏ→xanh QUA LÕI THẬT với dịch vụ may-dang-nhap GIẢ (Starlette chạy thật trên
127.0.0.1): mọi lời gọi HTTP của module đi qua mạng loopback thật. Các kiểu
tài khoản trong test đều GIẢ rõ ràng (@example.com).

Kịch bản dịch vụ giả (điều khiển qua fixture dich_vu_gia.che_do):
  can_nguoi_selfie | tai_khoan_bi_khoa | dang_nhap_bi_tu_choi
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

GOC = Path(__file__).resolve().parents[1]
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

from gia import MayChuThat  # noqa: E402
from app.tai_khoan_hang_loat import BiMatTam, BiMatTotp, DongDaTach, KetQuaDangNhap  # noqa: E402


def _tai_module():
    spec = importlib.util.spec_from_file_location(
        "may_dang_nhap_mod_nv209", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _tai_module()
EMAIL = "nguoi.dung@example.com"
MAT_KHAU = "mat-khau-gia-nv209-01"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


class DichVuGia:
    """Dịch vụ may-dang-nhap giả: kịch bản need_human điều khiển được."""

    def __init__(self):
        self.che_do = "can_nguoi_selfie"
        self.so_lan_goi = 0
        self._chuoi = []
        self._chi_so = 0

    def _dat_chuoi(self):
        che_do = self.che_do
        if che_do == "can_nguoi_selfie":
            self._chuoi = ["starting", "need_human"]
            self._loi = "xac_minh_selfie"
        elif che_do == "tai_khoan_bi_khoa":
            self._chuoi = ["starting", "need_human"]
            self._loi = "tai_khoan_bi_khoa"
        elif che_do == "dang_nhap_bi_tu_choi":
            self._chuoi = ["starting", "need_human"]
            self._loi = "dang_nhap_bi_tu_choi"
        elif che_do == "cham_dien_thoai":
            self._chuoi = ["starting", "running", "need_human"]
            self._loi = "xac_minh_dien_thoai"
        else:
            self._chuoi = ["starting", "failed"]
            self._loi = ""
        self._chi_so = 0

    def _tiep(self):
        if self._chi_so < len(self._chuoi) - 1:
            self._chi_so += 1
        return self._chuoi[self._chi_so]

    def app(self):
        dv = self

        async def health(request):
            return JSONResponse({"ok": True, "dich_vu": "may-dang-nhap",
                                 "status": "ok"})

        async def profiles(request):
            return JSONResponse({"profiles": []})

        async def luu_tai_khoan(request):
            than = await request.json()
            dv.so_lan_goi += 1
            return JSONResponse({"email": than.get("email"), "label": ""})

        async def bat_dau(request):
            than = await request.json()
            dv._dat_chuoi()
            dv.so_lan_goi += 1
            return JSONResponse({"profile": than.get("profile"),
                                 "state": "starting", "message": "Khởi tạo"})

        async def trang_thai(request):
            tt = dv._tiep()
            dv.so_lan_goi += 1
            tra = {"profile": request.path_params["profile"], "state": tt,
                   "message": f"giả: {tt}", "error": ""}
            if tt == "need_human":
                tra["error"] = dv._loi
                tra["message"] = "Cần người: giả"
            return JSONResponse(tra)

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", luu_tai_khoan, methods=["POST"]),
            Route("/v1/session/auto-login-saved", bat_dau, methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", trang_thai),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGia()
    with MayChuThat(dv.app()) as may_chu:
        dv.url = may_chu.url
        yield dv


@pytest.fixture
def mod(dich_vu_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    manifest = {"tien_to": "may_dang_nhap", "ten": "may-dang-nhap (test)"}
    m = MOD.Module(manifest)
    m._url = dich_vu_gia.url
    m._khoa = "khoa-gia"
    return m


def _dong(email=EMAIL, mk=MAT_KHAU, totp=TOTP_SECRET):
    return DongDaTach(dong=0, email=email, loai="mat_khau",
                      mat_khau=BiMatTam(mk),
                      totp=BiMatTotp(totp) if totp else None)


def _chay(coro):
    return asyncio.run(coro)


# ── BƯỚC 1 (đỏ trên mã cũ): need_human → can_nguoi + hướng dẫn ─────────────────

def test_can_nguoi_selfie_ma_loi_va_huong_dan(mod, dich_vu_gia):
    """Dịch vụ báo need_human/xac_minh_selfie → can_nguoi, mã lỗi giữ nguyên,
    hướng dẫn tiếng Việt cụ thể (mã cũ: module không biết need_human)."""
    dich_vu_gia.che_do = "can_nguoi_selfie"
    kq = _chay(mod.dang_nhap(_dong()))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong is False
    assert kq.ma_loi == "xac_minh_selfie"
    assert getattr(kq, "can_nguoi", False) is True
    huong_dan = getattr(kq, "huong_dan", "")
    assert "selfie" in huong_dan.lower()
    assert "đăng nhập lại" in huong_dan.lower()


def test_selfie_khong_phai_loi_vinh_vien_duoc_thu_lai(mod, dich_vu_gia):
    """xac_minh_selfie: người dùng có thể tự làm xong rồi thử lại → lần 2
    vẫn gọi dịch vụ (không bị chặn vĩnh viễn)."""
    dich_vu_gia.che_do = "can_nguoi_selfie"
    kq1 = _chay(mod.dang_nhap(_dong()))
    assert kq1.ma_loi == "xac_minh_selfie"
    goi_truoc = dich_vu_gia.so_lan_goi
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert kq2.ma_loi == "xac_minh_selfie"
    assert dich_vu_gia.so_lan_goi > goi_truoc, "lỗi tạm/cần người: được thử lại"


@pytest.mark.parametrize("che_do, ma_mong", [
    ("tai_khoan_bi_khoa", "tai_khoan_bi_khoa"),
    ("dang_nhap_bi_tu_choi", "dang_nhap_bi_tu_choi"),
])
def test_loi_vinh_vien_dung_han_khong_thu_lai(mod, dich_vu_gia, che_do, ma_mong):
    """Bị khoá / bị từ chối: đánh dấu vĩnh viễn, lần 2 không gọi mạng nữa."""
    dich_vu_gia.che_do = che_do
    kq1 = _chay(mod.dang_nhap(_dong()))
    assert kq1.thanh_cong is False
    assert kq1.ma_loi == ma_mong
    assert getattr(kq1, "can_nguoi", False) is True
    goi_truoc = dich_vu_gia.so_lan_goi
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert kq2.ma_loi == ma_mong
    assert dich_vu_gia.so_lan_goi == goi_truoc, "lỗi vĩnh viễn: dừng hẳn"


def test_cho_ket_qua_dung_ngay_khi_need_human(mod, dich_vu_gia):
    """_cho_ket_qua_dang_nhap coi need_human là trạng thái cuối — trả ngay,
    không chờ hết trần."""
    dich_vu_gia.che_do = "cham_dien_thoai"
    bat_dau = time.time()
    kq = _chay(mod.dang_nhap(_dong()))
    het = time.time() - bat_dau
    assert kq.ma_loi == "xac_minh_dien_thoai"
    assert getattr(kq, "can_nguoi", False) is True
    assert het < 20, f"phải trả ngay khi need_human (mất {het:.1f}s)"


def test_tien_do_anh_xa_need_human(mod, dich_vu_gia):
    dich_vu_gia.che_do = "can_nguoi_selfie"
    _chay(mod.dang_nhap(_dong()))
    # Giữ phiên ở need_human cho lần hỏi này
    dich_vu_gia._dat_chuoi()
    dich_vu_gia._chi_so = 1
    td = _chay(mod.tien_do(EMAIL))
    assert td["ok"] is True
    assert td["giai_doan"] == "can_nguoi"
    assert td["can_nguoi"] is True
    assert td["ma_loi"] == "xac_minh_selfie"


def test_ma_loi_la_khong_vo_huong_dan_mo_rong(mod):
    """Mã need_human lạ từ dịch vụ (tương lai) → vẫn can_nguoi, hướng dẫn chung."""
    st = mod._nv145b_trang_thai()
    kq = mod._ket_qua_tu_phien_dich_vu(
        {"state": "need_human", "error": "xac_minh_gi_do_moi",
         "message": "Cần người: giả"}, "khoa@x", st)
    assert kq.thanh_cong is False
    assert kq.ma_loi == "xac_minh_gi_do_moi"
    assert getattr(kq, "can_nguoi", False) is True
    assert getattr(kq, "huong_dan", ""), "phải có hướng dẫn chung"


# ── Qua lõi thật ──────────────────────────────────────────────────────────────

KHOA_QT = "khoa-quan-tri-test-nv209-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def moi_truong_loi(tmp_path, monkeypatch, dich_vu_gia):
    from fastapi.testclient import TestClient
    from app import so_module
    from app.main import app
    from app.so_module import SoModule
    from gia import ghi_thu_muc

    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    tep = {}
    for p in (GOC / "modules" / "may-dang-nhap").iterdir():
        if p.is_file():
            tep[p.name] = p.read_bytes()
    ghi_thu_muc(data_dir / "modules" / "may-dang-nhap", tep)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        r = client.post("/api/modules/nap-lai", headers=QT)
        assert r.status_code == 200, r.text
        r = client.post("/api/modules/may-dang-nhap/cai", headers=QT, json={
            "cau_hinh": {"dich_vu_url": dich_vu_gia.url,
                         "khoa_dich_vu": "khoa-gia"}})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True, r.text
        yield client, so


def test_tuyen_dang_nhap_can_nguoi_selfie_qua_loi_that(moi_truong_loi, dich_vu_gia):
    """POST /api/tai-khoan/da-luu/{id}/dang-nhap qua lõi thật khi dịch vụ báo
    need_human/xac_minh_selfie → can_nguoi + hướng dẫn, không lộ bí mật."""
    dich_vu_gia.che_do = "can_nguoi_selfie"
    client, so = moi_truong_loi
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL, "mat_khau": MAT_KHAU, "totp_secret": TOTP_SECRET,
        "nhan": "test nv209"})
    assert r.status_code == 200, r.text
    tai_khoan_id = r.json()["tai_khoan"]["id"]
    r = client.post(f"/api/tai-khoan/da-luu/{tai_khoan_id}/dang-nhap",
                    headers=QT, json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is False
    assert kq["can_nguoi"] is True
    assert kq["ma_loi"] == "xac_minh_selfie"
    assert "selfie" in kq["huong_dan"].lower()
    assert MAT_KHAU not in r.text
    assert "password" not in json.dumps(kq).lower()
