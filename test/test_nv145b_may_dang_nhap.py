"""Test NV145b — nối module may-dang-nhap vào nút «Lưu và đăng nhập» + tự đăng nhập lại.

Đỏ→xanh QUA LÕI THẬT với dịch vụ may-dang-nhap GIẢ (Starlette chạy thật trên
127.0.0.1): mọi lời gọi HTTP của module đi qua mạng loopback thật, không mock
httpx. Các kiểu tài khoản trong test đều GIẢ rõ ràng (@example.com).

Kịch bản dịch vụ giả (điều khiển qua fixture dich_vu_gia.che_do):
  thanh_cong | can_2fa | gap_captcha | sai_mat_khau | dang_chay_mai
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
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
        "may_dang_nhap_mod_nv145b", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _tai_module()
EMAIL = "nguoi.dung@example.com"
MAT_KHAU = "mat-khau-gia-nv145b-01"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


class DichVuGia:
    """Dịch vụ may-dang-nhap giả với kịch bản điều khiển được."""

    def __init__(self):
        self.che_do = "thanh_cong"
        self.da_luu = []          # thân POST /v1/accounts/saved
        self.ma_2fa = []          # mã đã gửi tới /auto-login-2fa-code
        self.so_lan_goi = 0
        self._chuoi = []
        self._chi_so = 0

    def _dat_chuoi(self):
        che_do = self.che_do
        if che_do == "thanh_cong":
            self._chuoi = ["starting", "running", "success"]
        elif che_do == "can_2fa":
            self._chuoi = ["starting", "need_code"]
        elif che_do == "gap_captcha":
            self._chuoi = ["starting", "need_captcha"]
        elif che_do == "sai_mat_khau":
            self._chuoi = ["starting", "running", "failed"]
        elif che_do == "dang_chay_mai":
            self._chuoi = ["starting", "running"]
        elif che_do == "loi_tam_thoi":
            self._chuoi = ["starting", "running", "failed"]
        else:
            self._chuoi = ["starting", "failed"]
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
            dv.da_luu.append(than)
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
            if tt == "failed" and dv.che_do == "sai_mat_khau":
                tra["error"] = "Sai mật khẩu — Google từ chối"
            if tt == "failed" and dv.che_do == "loi_tam_thoi":
                tra["error"] = "Lỗi mạng nội bộ thoáng qua"
            return JSONResponse(tra)

        async def ma_2fa(request):
            than = await request.json()
            dv.ma_2fa.append(than.get("code"))
            dv.so_lan_goi += 1
            return JSONResponse({"submitted": True})

        async def cookie(request):
            dv.so_lan_goi += 1
            return JSONResponse({"profile": request.path_params["profile"],
                                 "cookies": {"__Secure-1PSID": "psid-gia-145b",
                                             "__Secure-1PSIDTS": "psidts-gia-145b"}})

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", luu_tai_khoan, methods=["POST"]),
            Route("/v1/session/auto-login-saved", bat_dau, methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", trang_thai),
            Route("/v1/session/{profile}/auto-login-2fa-code", ma_2fa, methods=["POST"]),
            Route("/v1/gemini-web/{profile}/cookies", cookie),
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


# ── BƯỚC 1 (đỏ trên mã cũ): hợp đồng dang_nhap tồn tại ─────────────────────────

def test_hop_dong_dang_nhap_ton_tai(mod):
    """Module khai dang_nhap/nhap_ma_2fa theo hợp đồng lõi (mã cũ: không có)."""
    assert callable(getattr(mod, "dang_nhap", None))
    assert callable(getattr(mod, "nhap_ma_2fa", None))
    assert callable(getattr(mod, "tien_do", None))


# ── BƯỚC 2: các kịch bản qua dịch vụ giả ──────────────────────────────────────

def test_dang_nhap_thanh_cong(mod, dich_vu_gia):
    dich_vu_gia.che_do = "thanh_cong"
    dong = _dong()
    mk = dong.mat_khau
    kq = _chay(mod.dang_nhap(dong))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong is True
    assert kq.cookie is None  # spec: không trả cookie/token trong kết quả
    assert mk.da_xoa()  # bộ đệm bí mật đã ghi đè ngay sau khi dùng
    # Mật khẩu + TOTP đã tới két của dịch vụ (một kho gốc cho lượt đăng nhập)
    assert dich_vu_gia.da_luu, "phải POST /v1/accounts/saved"
    than = dich_vu_gia.da_luu[-1]
    assert than["email"] == EMAIL
    assert than["password"] == MAT_KHAU
    assert than["totp_secret"] == TOTP_SECRET
    assert than["loai"] == "google"


def test_dang_nhap_can_2fa_roi_gui_ma(mod, dich_vu_gia):
    dich_vu_gia.che_do = "can_2fa"
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong is False
    assert kq.ma_loi == "cho_2fa"
    assert getattr(kq, "cho_2fa", False) is True
    # Gửi mã 2FA qua hợp đồng nhap_ma_2fa
    kq2 = _chay(mod.nhap_ma_2fa(EMAIL, BiMatTam("123456")))
    assert kq2["ok"] is True
    assert dich_vu_gia.ma_2fa == ["123456"]
    # Mã sai định dạng bị từ chối trước khi gọi mạng
    kq3 = _chay(mod.nhap_ma_2fa(EMAIL, "abc"))
    assert kq3["ok"] is False


def test_dang_nhap_gap_captcha_can_nguoi(mod, dich_vu_gia):
    dich_vu_gia.che_do = "gap_captcha"
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong is False
    assert kq.ma_loi == "gap_captcha"
    assert getattr(kq, "can_nguoi", False) is True
    # Đã đánh dấu vĩnh viễn: lần 2 không gọi mạng nữa
    goi_truoc = dich_vu_gia.so_lan_goi
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert kq2.ma_loi == "gap_captcha"
    assert dich_vu_gia.so_lan_goi == goi_truoc


def test_dang_nhap_sai_mat_khau_vinh_vien(mod, dich_vu_gia):
    dich_vu_gia.che_do = "sai_mat_khau"
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong is False
    assert kq.ma_loi == "sai_mat_khau"
    assert getattr(kq, "can_nguoi", False) is True
    goi_truoc = dich_vu_gia.so_lan_goi
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert kq2.ma_loi == "sai_mat_khau"  # dừng hẳn, không thử lại mãi
    assert dich_vu_gia.so_lan_goi == goi_truoc


def test_dang_nhap_qua_tran_thu(mod, dich_vu_gia, monkeypatch):
    monkeypatch.setenv("C2A_DN_TRAN_THU_GIO", "1")
    dich_vu_gia.che_do = "thanh_cong"
    kq1 = _chay(mod.dang_nhap(_dong()))
    assert kq1.thanh_cong is True
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert kq2.thanh_cong is False
    assert kq2.ma_loi == "qua_tran_thu"


def test_dang_nhap_dich_vu_sap(mod, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod._url = "http://127.0.0.1:9"  # cổng chết: không ai nghe
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong is False
    assert kq.ma_loi == "dich_vu_sap"


def test_dang_nhap_tham_so_khong_hop_le(mod, dich_vu_gia):
    goi_truoc = dich_vu_gia.so_lan_goi
    kq = _chay(mod.dang_nhap(_dong(email="khong-phai-email")))
    assert kq.thanh_cong is False
    assert kq.ma_loi == "tham_so_khong_hop_le"
    assert dich_vu_gia.so_lan_goi == goi_truoc  # không chạm mạng khi tham số sai


def test_module_khong_giu_mat_khau(mod, dich_vu_gia):
    dich_vu_gia.che_do = "thanh_cong"
    _chay(mod.dang_nhap(_dong()))
    # Quét trạng thái module: không còn chuỗi mật khẩu nào sót lại
    def _quet(o, sau=0):
        if sau > 4:
            return False
        if isinstance(o, str):
            return MAT_KHAU in o
        if isinstance(o, dict):
            return any(_quet(v, sau + 1) for v in o.values())
        if isinstance(o, (list, tuple, set)):
            return any(_quet(v, sau + 1) for v in o)
        return False
    assert not _quet(mod.__dict__), "module giữ lại mật khẩu sau lượt gọi"


def test_tien_do_anh_xa_trang_thai(mod, dich_vu_gia):
    dich_vu_gia.che_do = "can_2fa"
    _chay(mod.dang_nhap(_dong()))
    td = _chay(mod.tien_do(EMAIL))
    assert td["ok"] is True
    assert td["giai_doan"] == "cho_2fa"
    assert td["cho_2fa"] is True
    # Hồ sơ lạ → tham số không hợp lệ, không lộ gì
    td2 = _chay(mod.tien_do("../etc"))
    assert td2["ok"] is False


def test_ket_qua_khong_chua_bi_mat(mod, dich_vu_gia):
    """Không mật khẩu/TOTP/cookie nào lọt vào bất kỳ thuộc tính chuỗi nào của kết quả."""
    dich_vu_gia.che_do = "thanh_cong"
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong is True
    for ten, gia_tri in vars(kq).items():
        if isinstance(gia_tri, str):
            assert MAT_KHAU not in gia_tri, f"mật khẩu lọt vào {ten}"
            assert TOTP_SECRET not in gia_tri, f"totp lọt vào {ten}"
    assert kq.cookie is None


def test_dang_nhap_loi_tam_thoi_duoc_thu_lai(mod, dich_vu_gia):
    """Lỗi tạm thời: không đánh dấu vĩnh viễn, lần sau được thử lại."""
    dich_vu_gia.che_do = "loi_tam_thoi"
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong is False
    assert kq.ma_loi == "dang_nhap_that_bai"
    assert getattr(kq, "can_nguoi", False) is False
    goi_truoc = dich_vu_gia.so_lan_goi
    dich_vu_gia.che_do = "thanh_cong"
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert kq2.thanh_cong is True  # được thử lại, không bị chặn
    assert dich_vu_gia.so_lan_goi > goi_truoc


def test_trinh_duyet_cookie_xuat_phien(mod, dich_vu_gia):
    kq = _chay(mod.trinh_duyet_cookie(EMAIL, "gemini.google.com"))
    assert "cookie" in kq
    assert "__Secure-1PSID=psid-gia-145b" in kq["cookie"]
    assert "__Secure-1PSIDTS=psidts-gia-145b" in kq["cookie"]
    # Miền không hỗ trợ → từ chối
    kq2 = _chay(mod.trinh_duyet_cookie(EMAIL, "evil.example"))
    assert kq2["ok"] is False


# ── QUA LÕI THẬT: TestClient + module cài thật ─────────────────────────────────

KHOA_QT = "khoa-quan-tri-test-nv145b-0123456789abcdef"
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


def test_loi_tinh_nang_bao_dang_nhap_tu_dong(moi_truong_loi):
    """Giao diện suy dang_nhap_tu_dong từ cấu trúc (có hàm dang_nhap) → may-dang-nhap
    hiện trong danh sách nguồn của nút «Lưu và đăng nhập» (mã cũ: false)."""
    client, so = moi_truong_loi
    r = client.get("/api/tinh-nang", headers=QT)
    assert r.status_code == 200, r.text
    muc = [m for m in r.json()["modules"] if m["id"] == "may-dang-nhap"]
    assert muc, "may-dang-nhap phải có trong tính năng"
    assert muc[0]["dang_nhap_tu_dong"] is True


def test_tuyen_dang_nhap_bang_tai_khoan_da_luu(moi_truong_loi, dich_vu_gia):
    """POST /api/tai-khoan/da-luu/{id}/dang-nhap {module_id: may-dang-nhap} qua lõi thật."""
    dich_vu_gia.che_do = "thanh_cong"
    client, so = moi_truong_loi
    # Lưu tài khoản vào kho lõi (bước «Lưu» của nút «Lưu và đăng nhập»)
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL, "mat_khau": MAT_KHAU, "totp_secret": TOTP_SECRET,
        "nhan": "test nv145b"})
    assert r.status_code == 200, r.text
    tai_khoan_id = r.json()["tai_khoan"]["id"]
    # Bấm «đăng nhập»: lõi tự lấy bí mật từ kho rồi gọi module.dang_nhap
    r = client.post(f"/api/tai-khoan/da-luu/{tai_khoan_id}/dang-nhap",
                    headers=QT, json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True, kq
    assert "password" not in json.dumps(kq)
    assert MAT_KHAU not in json.dumps(kq)
    # Mật khẩu đã tới két dịch vụ đúng một lần
    assert len(dich_vu_gia.da_luu) == 1
    assert dich_vu_gia.da_luu[0]["password"] == MAT_KHAU


def _luu_tai_khoan_qua_loi(client):
    """Lưu tài khoản vào kho lõi (bước «Lưu» của nút «Lưu và đăng nhập»)."""
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL, "mat_khau": MAT_KHAU, "totp_secret": TOTP_SECRET,
        "nhan": "test nv145b"})
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]["id"]


def test_tuyen_ma_2fa_qua_loi_that(moi_truong_loi, dich_vu_gia):
    """POST /api/tai-khoan/da-luu/{id}/ma-2fa → module.nhap_ma_2fa."""
    dich_vu_gia.che_do = "can_2fa"
    client, so = moi_truong_loi
    tai_khoan_id = _luu_tai_khoan_qua_loi(client)
    r = client.post(f"/api/tai-khoan/da-luu/{tai_khoan_id}/ma-2fa",
                    headers=QT, json={"module_id": "may-dang-nhap", "ma": "654321"})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True, r.text
    assert dich_vu_gia.ma_2fa == ["654321"]
    assert MAT_KHAU not in r.text and "654321" not in r.text
    # id lạ → 404, không lộ có/không
    r2 = client.post("/api/tai-khoan/da-luu/id-la-0123456789/ma-2fa",
                     headers=QT, json={"module_id": "may-dang-nhap", "ma": "654321"})
    assert r2.status_code == 404


def test_tuyen_trang_thai_dang_nhap_qua_loi_that(moi_truong_loi, dich_vu_gia):
    """GET /api/tai-khoan/da-luu/{id}/dang-nhap-trang-thai → module.tien_do."""
    dich_vu_gia.che_do = "can_2fa"
    client, so = moi_truong_loi
    tai_khoan_id = _luu_tai_khoan_qua_loi(client)
    # Bắt đầu một lượt đăng nhập để có phiên (không đợi xong)
    r = client.post(f"/api/tai-khoan/da-luu/{tai_khoan_id}/dang-nhap",
                    headers=QT, json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    assert r.json()["cho_2fa"] is True  # tuyến mới trả thêm cho_2fa
    # Hỏi trạng thái
    r2 = client.get(
        f"/api/tai-khoan/da-luu/{tai_khoan_id}/dang-nhap-trang-thai",
        params={"module_id": "may-dang-nhap"}, headers=QT)
    assert r2.status_code == 200, r2.text
    tt = r2.json()
    assert tt["ok"] is True
    assert tt["giai_doan"] == "cho_2fa"
    assert "password" not in r2.text and MAT_KHAU not in r2.text
