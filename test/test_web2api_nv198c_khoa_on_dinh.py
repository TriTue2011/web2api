"""NV198c — Đo: khoá tài khoản openaicodex phải ỔN ĐỊNH qua làm mới token.

BƯỚC 1 (đo trước, chưa sửa): 4 ca chạy qua LÕI THẬT
(kham_pha.kiem_manifest → nap_ma → ModuleKhamPha.tao → kho.kho_rieng)
với MÁY CHỦ OAUTH/CODEX GIẢ trên 127.0.0.1 (tái dùng của nv198a).

Mỗi ca đều: lấy khoá lõi đang giữ (tk.khoa trước làm mới) → ép làm mới
thành công → kiểm bằng khoá CŨ.
"""

from __future__ import annotations

import time

import pytest

from app import khoi_phuc_phien
from test_web2api_nv198a_oauth_do_khoang_cach import (  # noqa: F401
    chay,
    cai_voi_refresh_token,
    may_chu,
    mod,
    nap_module_qua_loi,
    tao_jwt,
)


def _ep_lam_moi(the, goi, may_chu, tk):
    """Ép làm mới thành công một lần, trả khoá/lõi trước đó để đối chiếu."""
    khoa_cu = tk.khoa
    id_cu = tk.id_thuc_the
    access_cu = tk.access_token
    goi.oauth.don_bo_dem_lam_moi()
    tk.expires_at = time.time() + 3600  # < ngưỡng 6 giờ → làm mới
    kq = chay(the.lam_moi_phien(khoa_cu))
    assert kq.thanh_cong is True, kq
    assert tk.access_token != access_cu, "chưa áp token mới sau làm mới"
    return khoa_cu, id_cu


# ---------------------------------------------------------------------------
# (a) Khoá lõi giữ không được xoay sau làm mới
# ---------------------------------------------------------------------------

def test_khoa_loi_giu_khong_doi_sau_lam_moi(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    khoa_cu, _id_cu = _ep_lam_moi(the, goi, may_chu, tk)
    assert tk.khoa == khoa_cu, (
        f"khoá đổi sau làm mới: {khoa_cu!r} → {tk.khoa!r}")
    # Lõi gọi lại bằng khoá cũ nó đang giữ phải trúng đúng tài khoản.
    assert the._tim_tai_khoan(khoa_cu) is tk, (
        "lõi giữ khoá cũ mà không tìm được tài khoản sau làm mới")
    kq = chay(the.lam_moi_phien(khoa_cu))
    assert kq.thanh_cong is True and kq.ma_loi != "khoa_khong_co", kq
    # danh_dau_hong / tai_khoan_ke bằng khoá cũ cũng phải đúng tài khoản.
    the.danh_dau_hong(khoa_cu, "401")
    assert tk.trang_thai == "vo_hieu", (
        "danh_dau_hong(khoá cũ) không đánh trúng tài khoản đã làm mới")


def test_id_thuc_the_khong_doi_sau_lam_moi(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    _khoa_cu, id_cu = _ep_lam_moi(the, goi, may_chu, tk)
    assert tk.id_thuc_the == id_cu, (
        f"id_thuc_the đổi sau làm mới: {id_cu!r} → {tk.id_thuc_the!r}")
    cac_the = chay(the.thuc_the())
    ids = [t["id"] for t in cac_the]
    assert ids.count(id_cu) == 1, (
        f"thực thể đổi id sau làm mới (danh sách: {ids})")


# ---------------------------------------------------------------------------
# (c) Trạng thái khôi phục của lõi còn đúng tài khoản sau làm mới
# ---------------------------------------------------------------------------

def test_trang_thai_loi_dung_khoa_cu_sau_lam_moi(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    khoa_cu, _id_cu = _ep_lam_moi(the, goi, may_chu, tk)
    # Lõi ghi trạng thái theo khoá nó giữ TRƯỚC khi làm mới...
    khoi_phuc_phien._cap_nhat("openaicodex", khoa_cu, hong_b_lien_tiep=2)
    # ...sau làm mới đọc lại bằng đúng khoá đó phải còn nguyên.
    tt = khoi_phuc_phien.lay_trang_thai("openaicodex", khoa_cu)
    assert tt.get("hong_b_lien_tiep") == 2, (
        f"trạng thái lõi theo khoá cũ bị lạc sau làm mới: {tt}")
    # Khoá tài khoản lõi liệt kê cũng phải là khoá cũ, không phải khoá ma.
    assert khoa_cu in the.khoa_tai_khoan(), (
        "khoa_tai_khoan() không còn chứa khoá lõi đang giữ")


# ---------------------------------------------------------------------------
# (d) Nạp lại kho (khởi động lại module) khoá không đổi
# ---------------------------------------------------------------------------

def test_khoa_khong_doi_qua_nap_lai_kho(mod, may_chu, monkeypatch):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    khoa_cu, _id_cu = _ep_lam_moi(the, goi, may_chu, tk)
    the._luu_tai_khoan_kho()
    # Module mới tinh nạp lại từ cùng kho dữ liệu (mô phỏng khởi động lại).
    the2, _goi2 = nap_module_qua_loi(monkeypatch)
    the2.kho = the.kho
    the2.nap({})
    assert len(the2._tai_khoan) == 1
    assert the2._tai_khoan[0].khoa == khoa_cu, (
        "nạp lại kho làm đổi khoá tài khoản")
    assert the2._tim_tai_khoan(khoa_cu) is the2._tai_khoan[0]
