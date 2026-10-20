"""NV198c — Đột biến cho khoá tài khoản ổn định.

Mỗi đột biến mô phỏng một cách «sửa sai» rồi khẳng định rằng bộ test đo
(test_web2api_nv198c_khoa_on_dinh.py) BẮT ĐƯỢC — tức test đi đỏ khi mã bị
đột biến theo hướng đó.
"""

from __future__ import annotations

import pytest

from test_web2api_nv198a_oauth_do_khoang_cach import (  # noqa: F401
    chay,
    cai_voi_refresh_token,
    may_chu,
    mod,
    tao_jwt,
)
from test_web2api_nv198c_khoa_on_dinh import _ep_lam_moi


def _dot_bien_va_kiem(ham_dot_bien, ham_kiem, mo_ta):
    """Áp đột biến, chạy kiểm; kiểm PHẢI đỏ (AssertionError) mới giết."""
    ham_dot_bien()
    with pytest.raises(AssertionError):
        ham_kiem()
    print(f"GIẾT: {mo_ta}")


# ---------------------------------------------------------------------------
# M1 — Quay lại khoá theo token: khoa = sha256(access_token)
# ---------------------------------------------------------------------------

def test_m1_quay_lai_khoa_theo_token_bi_bat(mod, may_chu, monkeypatch):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)

    def dot_bien():
        # Đột biến: khoa quay về dạng cũ sha256(access_token).
        monkeypatch.setattr(
            type(tk), "khoa",
            property(lambda self: goi._khoa_tu_token(self.access_token)))

    def kiem():
        khoa_cu = tk.khoa  # lúc này đã là dạng cũ (sha256 token hiện tại)
        _ep_lam_moi(the, goi, may_chu, tk)
        assert tk.khoa == khoa_cu, "khoá đổi sau làm mới"

    _dot_bien_va_kiem(dot_bien, kiem, "M1 quay lại khoá theo token")


# ---------------------------------------------------------------------------
# M2 — Hai tài khoản trùng mã bền: thao tác nhầm tài khoản
# ---------------------------------------------------------------------------

def test_m2_trung_ma_ben_bi_bat(mod, may_chu):
    the, _goi = mod
    a = cai_voi_refresh_token(the, may_chu)
    lop = type(a)
    b = lop(tao_jwt(email="nguoi.b@example.com"), "RT-B-1")
    b.ma_tai_khoan = a.ma_tai_khoan  # đột biến: trùng mã
    the._tai_khoan.append(b)

    def kiem():
        the.danh_dau_hong(b.khoa, "401")
        assert b.trang_thai == "vo_hieu", "không đánh trúng B"
        assert a.trang_thai == "hoat_dong", "đánh nhầm sang A vì trùng mã"

    with pytest.raises(AssertionError):
        kiem()
    print("GIẾT: M2 trùng mã bền giữa hai tài khoản")


# ---------------------------------------------------------------------------
# M3 — Mã không bền sau khởi động lại (sang_dict bỏ ma_tai_khoan)
# ---------------------------------------------------------------------------

def test_m3_ma_khong_ben_qua_khoi_dong_bi_bat(mod, may_chu, monkeypatch):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    khoa_cu = tk.khoa
    _ep_lam_moi(the, goi, may_chu, tk)

    def kiem():
        ds = [dict(d) for d in (tk.sang_dict(),)]
        ds[0].pop("ma_tai_khoan", None)  # đột biến: không lưu mã bền
        lop = type(tk)
        tk2 = lop(tk.access_token, tk.refresh_token)
        tk2.ap_dung_dict(ds[0])
        assert tk2.khoa == tk.khoa, (
            f"nạp lại làm đổi khoá: {tk.khoa!r} → {tk2.khoa!r}")

    with pytest.raises(AssertionError):
        kiem()
    print("GIẾT: M3 mã không bền sau khởi động lại")


# ---------------------------------------------------------------------------
# M4 — Nâng cấp tài khoản CŨ làm đổi khoá (fallback sai)
# ---------------------------------------------------------------------------

def test_m4_tai_khoan_cu_nang_cap_doi_khoa_bi_bat(mod, may_chu):
    the, _goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    lop = type(tk)
    khoa_cu = tk.khoa
    ban_ghi_cu = {k: v for k, v in tk.sang_dict().items()
                  if k != "ma_tai_khoan"}  # bản ghi thời «chưa có NV198c»

    def kiem():
        tk2 = lop(tk.access_token, tk.refresh_token)
        tk2.ap_dung_dict(ban_ghi_cu)
        assert tk2.khoa == khoa_cu, (
            f"nâng cấp tài khoản cũ đổi khoá: {khoa_cu!r} → {tk2.khoa!r}")
        # Và phải giữ nguyên ở lần nạp tiếp theo (không xoay mỗi lần nạp).
        tk3 = lop(tk.access_token, tk.refresh_token)
        tk3.ap_dung_dict(tk2.sang_dict())
        assert tk3.khoa == khoa_cu, "khoá không giữ nguyên qua lần nạp 2"

    # Đột biến mô phỏng: fallback gán mã NGẪU NHIÊN thay vì khoá hiện tại.
    import uuid

    def dot_bien():
        def ap_sai(self, d):
            if not d.get("ma_tai_khoan"):
                self.ma_tai_khoan = f"tk-{uuid.uuid4().hex[:16]}"
        lop.ap_dung_dict = ap_sai

    # Đột biến thay method trên LỚP — phải khôi phục sau test.
    ap_goc = lop.ap_dung_dict
    try:
        dot_bien()
        with pytest.raises(AssertionError):
            kiem()
    finally:
        lop.ap_dung_dict = ap_goc
    print("GIẾT: M4 nâng cấp tài khoản cũ làm đổi khoá")


# ---------------------------------------------------------------------------
# M5 — Bỏ kiểm trùng mã khi thêm tài khoản
# ---------------------------------------------------------------------------

def test_m5_bo_kiem_trung_ma_khi_them_bi_bat(mod, may_chu, monkeypatch):
    the, goi = mod
    _a = cai_voi_refresh_token(the, may_chu)
    lop = type(_a)

    # Đột biến: uuid luôn trả cùng một giá trị → mọi tài khoản mới đều
    # «muốn» cùng một mã bền. Mô phỏng mã bị đột biến «bỏ kiểm trùng»:
    # thêm mà KHÔNG qua vòng kiểm trùng của _them_hoac_cap_nhat.
    monkeypatch.setattr(goi.uuid, "uuid4",
                        lambda: type("U", (), {"hex": "0" * 32})())

    def kiem():
        ds = []
        for email in ("nguoi.x@example.com", "nguoi.y@example.com"):
            tkx = lop(tao_jwt(email=email), "RT-X")
            ds.append(tkx)  # đột biến: thêm thẳng, không kiểm trùng
        ids = [t.ma_tai_khoan for t in ds]
        assert len(set(ids)) == len(ids), (
            f"hai tài khoản khác nhau trùng mã bền: {ids}")

    with pytest.raises(AssertionError):
        kiem()
    print("GIẾT: M5 bỏ kiểm trùng mã khi thêm")

    # Và mã thật (có kiểm trùng) vẫn giữ tính duy nhất khi uuid lặp lại:
    # hai tài khoản mới đều «muốn» tk-000…0 → tài khoản sau được cứu bằng
    # hậu tố bộ đếm, không bao giờ trùng.
    x = the._them_hoac_cap_nhat(tao_jwt(email="nguoi.x@example.com"),
                                "RT-X-1")
    y = the._them_hoac_cap_nhat(tao_jwt(email="nguoi.y@example.com"),
                                "RT-Y-1")
    cac_ma = [t.ma_tai_khoan for t in the._tai_khoan]
    assert len(set(cac_ma)) == len(cac_ma), (
        f"vòng kiểm trùng không cứu được va chạm uuid: {cac_ma}")
    assert x.ma_tai_khoan != y.ma_tai_khoan


# ---------------------------------------------------------------------------
# M6 — id_thuc_the vẫn xoay theo token (sửa khoa mà quên id)
# ---------------------------------------------------------------------------

def test_m6_id_thuc_the_xoay_theo_token_bi_bat(mod, may_chu, monkeypatch):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    id_cu = tk.id_thuc_the

    def dot_bien():
        # Đột biến: id_thuc_the dùng sha256 token thay vì khoa ổn định.
        monkeypatch.setattr(
            type(tk), "id_thuc_the",
            property(lambda self: f"{goi.TIEN_TO}."
                     f"{goi._khoa_tu_token(self.access_token)}"))

    def kiem():
        _ep_lam_moi(the, goi, may_chu, tk)
        assert tk.id_thuc_the == id_cu, "id_thuc_the đổi sau làm mới"

    _dot_bien_va_kiem(dot_bien, kiem, "M6 id_thuc_the xoay theo token")
