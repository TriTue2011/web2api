"""NV197a — ĐO khoảng cách KHO TÀI KHOẢN AI giữa c2a và web2api (CHỈ ĐO, KHÔNG SỬA).

Mỗi test ánh xạ một chức năng của khối tài khoản c2a, chạy qua lõi thật
(TestClient) bằng tài khoản + nhà cung cấp GIẢ, ghi nhận web2api làm đúng hay
thiếu. Test ĐỎ = khoảng trống đã đo được (điều phối sẽ giao thành việc sửa
NV197b… riêng, mỗi ô một việc).

Ánh xạ c2a → test:
- account_service.add_accounts / POST /api/accounts            → test_them_sua_xoa_tai_khoan
- account_service.update_account {"status": "disabled"}        → test_tam_dung_tai_khoan (ĐỎ)
- nhãn tài khoản (kho_tai_khoan.nhan)                          → test_nhan_tai_khoan
- account_service.account_group                                → test_nhom_tai_khoan (ĐỎ)
- refresh_accounts (quét sức khoẻ định kỳ)                     → test_quet_suc_khoe_phien_dinh_ky
- quota_watcher / codex_usage                                  → test_theo_doi_han_muc (ĐỎ)
- usage_tracker.log_usage                                      → test_theo_doi_luong_dung (ĐỎ)
- get_available_access_token (hết hạn mức → tự chuyển)         → test_het_han_muc_tu_chuyen_tai_khoan
- remove_invalid_token (hết phiên → đánh dấu)                  → test_het_phien_danh_dau
- codex_error_recovery_scheduler (giới hạn khôi phục)           → test_khoi_phuc_gioi_han_2_lan_hong,
                                                                 test_khoi_phuc_tang_a_lam_moi
- GET /api/accounts (xuất, chỉ dạng che — cả hai cùng thiết kế) → test_xuat_chi_du_lieu_che
- POST /api/cpa|sub2api/.../import (nhập thật)                  → test_nhap_ap_dung_ghi_that_vao_kho (NV197e: XANH)
- POST /api/accounts/openai-hang-loat                          → test_dang_nhap_hang_loat
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv197a-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE_GIA = "gia-nv197"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    """Lõi THẬT: app đầy đủ + SoModule rỗng, dữ liệu cô lập trong tmp."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


def _cai_module_gia(so, fake):
    """Cài nhà cung cấp GIẢ vào lõi: catalog (đã cài) + dang_chay (đang chạy)."""
    fake.manifest = {"id": _MODULE_GIA}  # _id_cua_module lấy id từ manifest
    so.catalog[_MODULE_GIA] = SimpleNamespace(tien_to=_MODULE_GIA)
    so.dang_chay[_MODULE_GIA] = fake


def _them_tai_khoan(client, email, mat_khau="MatKhauGia123!", nhan=""):
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"email": email, "mat_khau": mat_khau, "nhan": nhan})
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


# ── (a) thêm / sửa / xoá ────────────────────────────────────────────────────

def test_them_sua_xoa_tai_khoan(moi_truong):
    """c2a add_accounts/delete_accounts → web2api luu/xoa qua HTTP: ĐẠT."""
    client, _so = moi_truong
    bk = _them_tai_khoan(client, "do-nv197a@example.com", nhan="do")
    tk_id = bk["id"]
    assert bk["nhan"] == "do"

    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200
    ds = r.json()["tai_khoan"]
    thay = next(b for b in ds if b["id"] == tk_id)
    assert thay["nhan"] == "do"
    assert "MatKhauGia123!" not in r.text  # bí mật không lộ

    # SỬA: trùng email → cập nhật đúng bản ghi cũ
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "do-nv197a@example.com", "mat_khau": "MatKhauMoi456!",
        "nhan": "do-sua"})
    assert r.json()["tai_khoan"]["id"] == tk_id
    assert r.json()["tai_khoan"]["nhan"] == "do-sua"

    # XOÁ
    r = client.delete(f"/api/tai-khoan/da-luu/{tk_id}", headers=_QT)
    assert r.status_code == 200 and r.json()["da_xoa"] is True
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert all(b["id"] != tk_id for b in r.json()["tai_khoan"])
    # Xoá lại id đã mất → 404, không lộ có/không
    r = client.delete(f"/api/tai-khoan/da-luu/{tk_id}", headers=_QT)
    assert r.status_code == 404


def test_tam_dung_tai_khoan(moi_truong):
    """c2a update_account {"status": "disabled"} → web2api KHÔNG CÓ: ĐỎ."""
    client, _so = moi_truong
    bk = _them_tai_khoan(client, "tam-dung@example.com")
    # Không có API tạm dừng riêng; thử gửi trang_thai qua đường lưu —
    # route bỏ qua trường này nên không thể tạm dừng qua API.
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "id": bk["id"], "email": "tam-dung@example.com",
        "mat_khau": "MatKhauGia123!", "trang_thai": "tam_dung"})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"].get("trang_thai") == "tam_dung", \
        "web2api thiếu tạm dừng tài khoản (c2a: status=disabled, bộ chọn bỏ qua)"


# ── (b) nhãn / nhóm ──────────────────────────────────────────────────────────

def test_nhan_tai_khoan(moi_truong):
    """Nhãn đơn `nhan`: web2api có → ĐẠT."""
    client, _so = moi_truong
    _them_tai_khoan(client, "nhan@example.com", nhan="nhom-do")
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    thay = next(b for b in r.json()["tai_khoan"]
                if b["email_che"].endswith("@example.com"))
    assert thay["nhan"] == "nhom-do"


@pytest.mark.xfail(reason="khoảng trống đã đo (NV197a ô #4: nhóm tài khoản/bậc gói); chưa giao việc sửa", strict=False)
def test_nhom_tai_khoan(moi_truong):
    """c2a account_group (free/codex/openai/…) → web2api KHÔNG CÓ: ĐỎ."""
    client, _so = moi_truong
    _them_tai_khoan(client, "nhom@example.com")
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    bk = r.json()["tai_khoan"][0]
    assert "nhom" in bk, \
        "web2api thiếu phân nhóm tài khoản (c2a: account_service.account_group)"


# ── (c) kiểm sức khoẻ định kỳ ───────────────────────────────────────────────

def test_quet_suc_khoe_phien_dinh_ky(moi_truong):
    """c2a refresh_accounts → web2api phien.quet_mot_vong: ĐẠT cho PHIÊN.

    Ghi chú khoảng trống: đây là quét PHIÊN (session) của module đang chạy,
    kho tài khoản kho_tai_khoan KHÔNG có vòng kiểm sức định kỳ riêng.
    """
    client, so = moi_truong
    from app import phien as phien_mod

    class ModuleGiaChet:
        async def phien_con_song(self, du_lieu):
            return False

    so.dang_chay[_MODULE_GIA] = ModuleGiaChet()
    phien_mod.luu(_MODULE_GIA, "tk1", {"cookie": "gia"})
    kq = asyncio.run(phien_mod.quet_mot_vong(so))
    assert kq["da_quet"] >= 1, kq
    ban_ghi = phien_mod.lay(_MODULE_GIA, "tk1")
    assert ban_ghi["trang_thai"] == "can_dang_nhap", ban_ghi
    assert ban_ghi["so_lan_loi"] >= 1


# ── (d) hạn mức + lượng dùng ────────────────────────────────────────────────

def test_theo_doi_han_muc(moi_truong):
    """c2a quota_watcher/codex_usage → web2api KHÔNG CÓ: ĐỎ."""
    client, _so = moi_truong
    _them_tai_khoan(client, "hanmuc@example.com")
    r = client.get("/api/tai-khoan/han-muc", headers=_QT)
    assert r.status_code == 200, \
        "web2api thiếu theo dõi hạn mức (c2a: quota_watcher, codex_usage.ghi_nhan_tu_header)"
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert "han_muc" in r.json()["tai_khoan"][0]


def test_theo_doi_luong_dung(moi_truong):
    """c2a usage_tracker.log_usage → web2api KHÔNG CÓ (theo tài khoản): ĐỎ."""
    client, _so = moi_truong
    r = client.get("/api/tai-khoan/luong-dung", headers=_QT)
    assert r.status_code == 200, \
        "web2api thiếu thống kê lượng dùng theo tài khoản (c2a: usage_tracker)"


# ── (e) hết hạn mức → tự chuyển tài khoản ────────────────────────────────────

def test_het_han_muc_tu_chuyen_tai_khoan(moi_truong):
    """c2a get_available_access_token → web2api tầng (c) tai_khoan_ke: ĐẠT.

    Ghi chú khoảng trống: lõi không biết nguyên nhân là «hết hạn mức» —
    module tự quyết khi nào kích hoạt khôi phục; không có phát hiện 429 →
    chuyển tự động ở tầng lõi.
    """
    client, so = moi_truong
    from app import khoi_phuc_phien as kp

    da_danh_dau = []

    class ModuleGiaHetHanMuc:
        async def lam_moi_phien(self, khoa):
            return kp.KetQuaKhoiPhuc(False, tang=kp.TANG_A,
                                     ma_loi="het_han_muc")

        def tai_khoan_ke(self, khoa):
            return "khoa-2"

        def danh_dau_hong(self, khoa, ma_loi):
            da_danh_dau.append((khoa, ma_loi))

    _cai_module_gia(so, ModuleGiaHetHanMuc())
    r = client.post("/api/tai-khoan/khoi-phuc", headers=_QT,
                    json={"module_id": _MODULE_GIA, "khoa": "khoa-1"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["tang"] == "c", kq
    assert kq["khoa_moi"] == "khoa-2", kq
    assert da_danh_dau == [("khoa-1", "het_han_muc")]


# ── (f) hết phiên → đánh dấu + khôi phục có giới hạn ─────────────────────────

def test_het_phien_danh_dau(moi_truong):
    """c2a remove_invalid_token → web2api kiem phiên đánh dấu: ĐẠT."""
    client, so = moi_truong
    from app import phien as phien_mod

    class ModuleGiaBatKy:
        manifest = {"id": _MODULE_GIA}

        async def phien_con_song(self, du_lieu):
            return True

    # Cần module «đang chạy» CÓ phien_con_song — nếu không kiem_mot_phien trả
    # «chua_ro» trước khi tới bước kiểm hết hạn, lệch mục tiêu đo.
    so.dang_chay[_MODULE_GIA] = ModuleGiaBatKy()

    het_han = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    phien_mod.luu(_MODULE_GIA, "tkhet", {"cookie": "gia"}, het_han=het_han)
    r = client.post(f"/api/phien/{_MODULE_GIA}/tkhet/kiem", headers=_QT)
    assert r.status_code == 200, r.text
    assert r.json()["trang_thai"] == "can_dang_nhap", r.json()


def test_khoi_phuc_tang_a_lam_moi(moi_truong):
    """Tầng (a) làm mới phiên bằng cookie: ĐẠT."""
    client, so = moi_truong
    from app import khoi_phuc_phien as kp

    class ModuleGiaLamMoi:
        async def lam_moi_phien(self, khoa):
            return kp.KetQuaKhoiPhuc(True, tang=kp.TANG_A)

    _cai_module_gia(so, ModuleGiaLamMoi())
    r = client.post("/api/tai-khoan/khoi-phuc", headers=_QT,
                    json={"module_id": _MODULE_GIA, "khoa": "khoa-a"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True and kq["tang"] == "a", kq


def test_khoi_phuc_gioi_han_2_lan_hong(moi_truong):
    """c2a scheduler giới hạn khôi phục → web2api HONG_B_TOI_DA=2 + can_nguoi: ĐẠT."""
    client, so = moi_truong
    from app import khoi_phuc_phien as kp
    from app.tai_khoan_hang_loat import BiMatTam

    class ModuleGiaHong:
        def mat_khau_da_luu(self, khoa):
            return BiMatTam("mat-khau-gia")

        async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
            return kp.KetQuaKhoiPhuc(False, tang=kp.TANG_B,
                                     ma_loi="sai_mat_khau")

    _cai_module_gia(so, ModuleGiaHong())
    r1 = client.post("/api/tai-khoan/khoi-phuc", headers=_QT,
                     json={"module_id": _MODULE_GIA, "khoa": "khoa-hong"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["tang"] == "d" and r1.json()["can_nguoi"] is True, r1.json()

    r2 = client.post("/api/tai-khoan/khoi-phuc", headers=_QT,
                     json={"module_id": _MODULE_GIA, "khoa": "khoa-hong"})
    assert r2.json()["tang"] == "d", r2.json()

    r = client.get("/api/tai-khoan/khoi-phuc/trang-thai", headers=_QT,
                   params={"module_id": _MODULE_GIA, "khoa": "khoa-hong"})
    assert r.status_code == 200, r.text
    assert r.json().get("ngung_tu_dang_nhap") is True, r.json()

    # Người xử lý tay xong → xoá trạng thái cần-người được
    r = client.post("/api/tai-khoan/khoi-phuc/xoa-can-nguoi", headers=_QT,
                    json={"module_id": _MODULE_GIA, "khoa": "khoa-hong"})
    assert r.json()["ok"] is True


# ── (g) xuất / nhập ─────────────────────────────────────────────────────────

def test_xuat_chi_du_lieu_che(moi_truong):
    """Xuất: cả hai đều KHÔNG xuất bí mật thô (thiết kế) → ĐẠT."""
    client, _so = moi_truong
    client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "xuat@example.com", "mat_khau": "BiMatThoDoNv197a!",
        "totp_secret": "JBSWY3DPEHPK3PXP", "nhan": "xuat"})
    txt = client.get("/api/tai-khoan/da-luu", headers=_QT).text
    assert "BiMatThoDoNv197a!" not in txt
    assert "JBSWY3DPEHPK3PXP" not in txt
    ds = json.loads(txt)["tai_khoan"]
    assert ds[0]["co_mat_khau"] is True and ds[0]["co_totp"] is True


def test_nhap_ap_dung_ghi_that_vao_kho(moi_truong):
    """c2a import từ cpa/sub2api → web2api ap_dung GHI THẬT vào kho (NV197e).

    (Trước NV197e test này tên test_nhap_ap_dung_tu_choi, assert từ chối
    «chua_noi_kho_tai_khoan» — hành vi đã đổi có chủ ý nên cập nhật theo.)"""
    import pathlib

    from modules.nhaptaikhoan.module import Module

    manifest = json.loads(pathlib.Path("modules/nhaptaikhoan/manifest.json")
                          .read_text(encoding="utf-8"))
    mod = Module(manifest)
    from app import ngu_canh
    tok = ngu_canh.dat({"loai": "quan_tri", "id": "qt-test",
                        "pham_vi": ["*"]}, nguon="nguoi")
    try:
        kq = asyncio.run(mod.thuc_hien(
            "nhaptaikhoan.ap_dung", "ap_dung",
            {"danh_sach": [{"nguon": "sub2api", "dinh_danh": "tk-gia-1",
                            "bi_mat": "bi-mat-gia"}]}))
    finally:
        ngu_canh.khoi_phuc(tok)
    assert kq.get("ok") is True, \
        f"nhập thật vào kho chưa nối được (c2a: import cpa/sub2api): {kq}"
    assert kq.get("da_them") == 1


def test_dang_nhap_hang_loat(moi_truong):
    """c2a POST /api/accounts/openai-hang-loat → web2api dang-nhap-hang-loat: ĐẠT."""
    client, so = moi_truong
    from app import tai_khoan_hang_loat as tkl

    class ModuleGiaDangNhap:
        async def dang_nhap(self, tai_khoan: tkl.DongDaTach) -> tkl.KetQuaDangNhap:
            assert tai_khoan.email == "hangloat@example.com"
            return tkl.KetQuaDangNhap(thanh_cong=True, cookie="phien-gia")

    _cai_module_gia(so, ModuleGiaDangNhap())
    r = client.post("/api/tai-khoan/dang-nhap-hang-loat/bat-dau", headers=_QT,
                    json={"module_id": _MODULE_GIA,
                          "van_ban": "hangloat@example.com|MatKhau123!"})
    assert r.status_code == 200, r.text

    tt = None
    for _ in range(200):
        tt = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai",
                        headers=_QT).json()
        if tt.get("trang_thai_luot") == "hoan_thanh":
            break
        time.sleep(0.05)
    assert tt is not None and tt["tom_tat"]["xong"] == 1, tt
    client.post("/api/tai-khoan/dang-nhap-hang-loat/dung", headers=_QT)
