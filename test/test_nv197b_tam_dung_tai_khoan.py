"""NV197b — TẠM DỪNG / BẬT LẠI tài khoản AI đã lưu (ô đỏ #2 của NV197a).

c2a: account_service.update_account {"status": "disabled"} → bộ chọn bỏ qua;
web2api: trường kho `trang_thai` (""/"tam_dung"), đường riêng
POST /api/tai-khoan/da-luu/{id}/tam-dung (step-up), lõi thu_dang_nhap_lai và
đường /dang-nhap bỏ qua tài khoản tạm dừng, giao diện nút «Tạm dừng»/«Bật lại».

Chạy qua lõi thật (TestClient), nhà cung cấp GIẢ, không mạng ngoài.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import kho as kho_mod
from app import kho_tai_khoan as ktk
from app.main import app
from app.so_module import SoModule
from app.tai_khoan_hang_loat import KetQuaDangNhap

_KHOA_QT = "khoa-qt-nv197b-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE_GIA = "gia-nv197b"
MK_ADMIN = "MatKhauAdminGia000000000000"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from app.main import app as _app

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(_app) as client:
        client.app.state.so = SoModule()
        yield client


class _ModuleGia:
    """Module giả: dang_nhap ghi lại đã được gọi bao nhiêu lần."""

    def __init__(self):
        self.so_lan_dang_nhap = 0

    async def dang_nhap(self, tai_khoan):
        self.so_lan_dang_nhap += 1
        return KetQuaDangNhap(True)


def _cai_module_gia(client):
    from types import SimpleNamespace

    mod = _ModuleGia()
    mod.manifest = {"id": _MODULE_GIA}
    so = client.app.state.so
    so.catalog[_MODULE_GIA] = SimpleNamespace(tien_to=_MODULE_GIA)
    so.dang_chay[_MODULE_GIA] = mod
    return mod


def _them(client, email, mat_khau="MatKhauGia123!", trang_thai=None):
    payload = {"email": email, "mat_khau": mat_khau}
    if trang_thai is not None:
        payload["trang_thai"] = trang_thai
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=payload)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


def _phien_admin(client):
    from app import nguoi_dung

    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-nv197b",
                                         mat_khau=MK_ADMIN))
    r = client.post("/api/dang-nhap",
                    json={"ten_dang_nhap": "admin-nv197b", "mat_khau": MK_ADMIN})
    assert r.status_code == 200, r.text
    return {"w2a_phien": r.cookies.get("w2a_phien")}, \
        {"X-CSRF-Token": r.json()["csrf"]}


# ── (1) ca đỏ gốc NV197a → xanh ─────────────────────────────────────────────

def test_tam_dung_tai_khoan(moi_truong):
    """Ca đỏ NV197a: gửi trang_thai qua đường lưu → kho giữ và trả lại."""
    client = moi_truong
    bk = _them(client, "tam-dung@example.com")
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "id": bk["id"], "email": "tam-dung@example.com",
        "mat_khau": "MatKhauGia123!", "trang_thai": "tam_dung"})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"].get("trang_thai") == "tam_dung"
    ds = client.get("/api/tai-khoan/da-luu", headers=_QT).json()["tai_khoan"]
    assert next(b for b in ds if b["id"] == bk["id"])["trang_thai"] == "tam_dung"


# ── (2) đường riêng đặt/gỡ cờ ────────────────────────────────────────────────

def test_api_tam_dung_rieng_dat_va_go(moi_truong):
    client = moi_truong
    bk = _them(client, "rieng@example.com")
    assert bk["trang_thai"] == ""  # mặc định bật

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                    json={"tam_dung": True})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"]["trang_thai"] == "tam_dung"

    # Lưu bền: đọc thô từ kho vẫn thấy
    tho = kho_mod.doc_bi_mat(ktk.TEN_KHO)
    assert tho[bk["id"]]["trang_thai"] == "tam_dung"

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                    json={"tam_dung": False})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"]["trang_thai"] == ""
    assert kho_mod.doc_bi_mat(ktk.TEN_KHO)[bk["id"]]["trang_thai"] == ""


def test_tam_dung_yeu_cau_boolean_that(moi_truong):
    client = moi_truong
    bk = _them(client, "bool@example.com")
    for xau in ({"tam_dung": "true"}, {"tam_dung": 1}, {}):
        r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung",
                        headers=_QT, json=xau)
        assert r.status_code == 400, (xau, r.text)
    assert kho_mod.doc_bi_mat(ktk.TEN_KHO)[bk["id"]]["trang_thai"] == ""


def test_trang_thai_la_bi_tu_choi(moi_truong):
    """Danh sách giá trị đóng: chỉ "" và "tam_dung" được chấp nhận."""
    client = moi_truong
    bk = _them(client, "la@example.com")
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "id": bk["id"], "email": "la@example.com",
        "mat_khau": "MatKhauGia123!", "trang_thai": "da_xoa"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_value"
    assert kho_mod.doc_bi_mat(ktk.TEN_KHO)[bk["id"]]["trang_thai"] == ""


# ── (3) 401 / 403 / IDOR ────────────────────────────────────────────────────

def test_khong_khoa_401(moi_truong):
    client = moi_truong
    bk = _them(client, "a401@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung",
                    json={"tam_dung": True})
    assert r.status_code == 401, r.text


def test_khoa_khach_403(moi_truong):
    client = moi_truong
    from app import khoa_khach as kk_mod

    _muc, khoa_tho = kk_mod.tao_khoa(nhan="khach-nv197b")
    hk = {"Authorization": f"Bearer {khoa_tho}"}
    r = client.post("/api/tai-khoan/da-luu/tk-0000000000000000/tam-dung",
                    headers=hk, json={"tam_dung": True})
    assert r.status_code == 403, r.text


def test_step_up_403_va_xac_nhan_lai_200(moi_truong):
    client = moi_truong
    bk = _them(client, "stepup@example.com")
    ck, h = _phien_admin(client)
    duong = f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung"
    r = client.post(duong, json={"tam_dung": True}, cookies=ck, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "step_up_required"
    # Chưa xác nhận lại → cờ KHÔNG đổi
    assert kho_mod.doc_bi_mat(ktk.TEN_KHO)[bk["id"]]["trang_thai"] == ""
    r = client.post("/api/toi/xac-nhan-lai", json={"mat_khau": MK_ADMIN},
                    cookies=ck, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(duong, json={"tam_dung": True}, cookies=ck, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"]["trang_thai"] == "tam_dung"


def test_idor_cach_ly_theo_id_va_404(moi_truong):
    """Người A không ảnh hưởng B: đặt cờ A, B giữ nguyên; id lạ → 404."""
    client = moi_truong
    a = _them(client, "a-idor@example.com")
    b = _them(client, "b-idor@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{a['id']}/tam-dung", headers=_QT,
                    json={"tam_dung": True})
    assert r.status_code == 200
    tho = kho_mod.doc_bi_mat(ktk.TEN_KHO)
    assert tho[a["id"]]["trang_thai"] == "tam_dung"
    assert tho[b["id"]]["trang_thai"] == ""
    r = client.post("/api/tai-khoan/da-luu/tk-ffffffffffffffff/tam-dung",
                    headers=_QT, json={"tam_dung": True})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "khong_tim_thay"


# ── (4) lõi bỏ qua tài khoản tạm dừng ────────────────────────────────────────

def test_thu_dang_nhap_lai_bo_qua_tam_dung(moi_truong):
    """Lõi thật: tài khoản tạm dừng → ly_do tam_dung, module KHÔNG được gọi."""
    client = moi_truong
    mod = _cai_module_gia(client)
    email = "loi@example.com"
    bk = _them(client, email)
    client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                json={"tam_dung": True})

    kq = asyncio.run(ktk.thu_dang_nhap_lai(
        module=mod, module_id=_MODULE_GIA, dinh_danh=email))
    assert kq == {"thu": False, "ly_do": "tam_dung"}, kq
    assert mod.so_lan_dang_nhap == 0  # không chạm bí mật, không gọi module


def test_bat_lai_thu_dang_nhap_lai_thanh_cong(moi_truong):
    """Bật lại → lõi chọn lại bình thường."""
    client = moi_truong
    mod = _cai_module_gia(client)
    email = "batlai@example.com"
    bk = _them(client, email)
    duong = f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung"
    client.post(duong, headers=_QT, json={"tam_dung": True})
    assert asyncio.run(ktk.thu_dang_nhap_lai(
        module=mod, module_id=_MODULE_GIA, dinh_danh=email))["ly_do"] == "tam_dung"
    client.post(duong, headers=_QT, json={"tam_dung": False})
    kq = asyncio.run(ktk.thu_dang_nhap_lai(
        module=mod, module_id=_MODULE_GIA, dinh_danh=email))
    assert kq["thu"] is True and kq["thanh_cong"] is True, kq
    assert mod.so_lan_dang_nhap == 1


def test_tat_ca_tam_dung_khong_dung_am_tham(moi_truong):
    """Tất cả đều tạm dừng → lỗi rõ ràng, không tài khoản nào bị dùng."""
    client = moi_truong
    mod = _cai_module_gia(client)
    ids = []
    for em in ("het1@example.com", "het2@example.com"):
        bk = _them(client, em)
        ids.append(bk["id"])
        client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                    json={"tam_dung": True})
    for em in ("het1@example.com", "het2@example.com"):
        kq = asyncio.run(ktk.thu_dang_nhap_lai(
            module=mod, module_id=_MODULE_GIA, dinh_danh=em))
        assert kq == {"thu": False, "ly_do": "tam_dung"}, (em, kq)
    assert mod.so_lan_dang_nhap == 0
    # Bật lại một → chỉ tài khoản đó được dùng
    client.post(f"/api/tai-khoan/da-luu/{ids[0]}/tam-dung", headers=_QT,
                json={"tam_dung": False})
    kq = asyncio.run(ktk.thu_dang_nhap_lai(
        module=mod, module_id=_MODULE_GIA, dinh_danh="het1@example.com"))
    assert kq["thu"] is True, kq
    assert mod.so_lan_dang_nhap == 1


def test_dang_nhap_route_tu_choi_tam_dung(moi_truong):
    """Đường /dang-nhap: tài khoản tạm dừng → 403 rõ ràng; bật lại → chạy."""
    client = moi_truong
    mod = _cai_module_gia(client)
    bk = _them(client, "dangnhap@example.com")
    duong_dn = f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap"
    client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                json={"tam_dung": True})
    r = client.post(duong_dn, headers=_QT, json={"module_id": _MODULE_GIA})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "tam_dung"
    assert "tạm dừng" in r.json()["error"]["message"]
    assert mod.so_lan_dang_nhap == 0
    client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                json={"tam_dung": False})
    r = client.post(duong_dn, headers=_QT, json={"module_id": _MODULE_GIA})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert mod.so_lan_dang_nhap == 1


# ── (5) hiển thị trạng thái + giao diện ──────────────────────────────────────

def test_ban_cong_khai_hien_trang_thai(moi_truong):
    client = moi_truong
    bk = _them(client, "hien@example.com")
    assert bk["trang_thai"] == ""
    ds = client.get("/api/tai-khoan/da-luu", headers=_QT).json()["tai_khoan"]
    assert next(b for b in ds if b["id"] == bk["id"])["trang_thai"] == ""
    client.post(f"/api/tai-khoan/da-luu/{bk['id']}/tam-dung", headers=_QT,
                json={"tam_dung": True})
    ds = client.get("/api/tai-khoan/da-luu", headers=_QT).json()["tai_khoan"]
    b = next(x for x in ds if x["id"] == bk["id"])
    assert b["trang_thai"] == "tam_dung"
    # Không lộ bí mật trong phản hồi liệt kê
    assert "mat_khau" not in b and "totp_secret" not in b


def test_giao_dien_co_nut_tam_dung_bat_lai():
    """Giao diện: nút Tạm dừng/Bật lại + chữ «Đã tạm dừng», tiếng Việt đủ dấu."""
    from pathlib import Path

    ma = (Path(__file__).resolve().parents[1] / "web" / "He-thong-AI.dc.html"
          ).read_text(encoding="utf-8")
    assert "api/tai-khoan/da-luu/" in ma and "/tam-dung" in ma
    assert "Tạm dừng" in ma and "Bật lại" in ma and "Đã tạm dừng" in ma
    assert "anTamDung" in ma  # chưa có đường trên máy chủ cũ → ẩn nút
