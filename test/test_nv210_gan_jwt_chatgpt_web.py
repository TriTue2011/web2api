"""NV210 (điều phối nối) — chatgpt-web.gan_jwt_tu_dang_nhap: đưa JWT mới + hồ sơ vào khoá sổ BỀN.

Module chatgpt-web THẬT (nạp như test NV207), kho cô lập trong tmp. Token/email đều GIẢ.
Nhận diện tài khoản bằng EMAIL: đúng một khoá cùng email → thay; chưa có → thêm; nhiều khoá cùng email → từ chối
(mơ hồ, không đoán). Mọi trường hợp đọc lại cấu hình ĐÃ LƯU để chắc không chỉ đổi trong bộ nhớ."""
from __future__ import annotations

import pytest

from app import kho
from test_web2api_nv207_lam_moi_jwt import MANIFEST, Module, _jwt_gia, mod_module

EMAIL = "nguoi-nv210@example.com"
HS = "chatgpt-hs-nv210"


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("chatgpt-web")
    return m


def _gan(mod, *cap):
    """cap = [(token, email, ho_so)] — dựng sẵn các tài khoản trong bộ nhớ."""
    with mod._khoa:
        mod._tai_khoan = []
        for token, email, ho_so in cap:
            tk = mod_module._TaiKhoan(token)
            tk.email = email
            tk.ho_so = ho_so
            mod._tai_khoan.append(tk)


def _da_luu(mod):
    truong = mod._truong_cau_hinh_jwt()
    ds_mod, _ = kho.doc_modules()
    muc = next((m for m in ds_mod or [] if isinstance(m, dict) and m.get("id") == "chatgpt-web"), None)
    return (kho.doc_cau_hinh("chatgpt-web", truong, muc) if muc else {}).get("tai_khoan") or []


def test_thay_khoa_cu_cung_email_va_gan_ho_so_ben(mod):
    cu, moi = _jwt_gia(extra={"v": 1}), _jwt_gia(extra={"v": 2})
    _gan(mod, (cu, EMAIL, None), (_jwt_gia(email="khac@example.com"), "khac@example.com", None))
    kq = mod.gan_jwt_tu_dang_nhap(EMAIL, moi, HS)
    assert kq["ok"] is True and kq["thay_the"] is True, kq
    with mod._khoa:
        tokens = [t.token for t in mod._tai_khoan]
        tk = next(t for t in mod._tai_khoan if t.token == moi)
    assert cu not in tokens and len(tokens) == 2, "thay đúng khoá của tài khoản đó, không đụng khoá khác"
    assert tk.ho_so == HS and tk.email == EMAIL
    luu = _da_luu(mod)
    assert moi in luu and cu not in luu, "phải ghi BỀN vào cấu hình, không chỉ bộ nhớ"


def test_chua_co_khoa_thi_them_moi_ben(mod):
    moi = _jwt_gia(extra={"v": 3})
    _gan(mod)
    kq = mod.gan_jwt_tu_dang_nhap(EMAIL, moi, HS)
    assert kq["ok"] is True and kq["thay_the"] is False, kq
    with mod._khoa:
        assert [t.token for t in mod._tai_khoan] == [moi]
        assert mod._tai_khoan[0].ho_so == HS
    assert moi in _da_luu(mod)


def test_nhieu_khoa_cung_email_thi_tu_choi_khong_doan(mod):
    a, b, moi = _jwt_gia(extra={"v": 1}), _jwt_gia(extra={"v": 2}), _jwt_gia(extra={"v": 9})
    _gan(mod, (a, EMAIL, None), (b, EMAIL, None))
    kq = mod.gan_jwt_tu_dang_nhap(EMAIL, moi, HS)
    assert kq["ok"] is False and kq["ma_loi"] == "mo_ho_nhieu_khoa_cung_email"
    with mod._khoa:
        assert sorted(t.token for t in mod._tai_khoan) == sorted([a, b])


def test_da_co_chinh_token_thi_chi_gan_ho_so(mod):
    t = _jwt_gia(extra={"v": 4})
    _gan(mod, (t, EMAIL, None))
    kq = mod.gan_jwt_tu_dang_nhap(EMAIL, t, HS)
    assert kq["ok"] is True and kq["thay_the"] is False
    with mod._khoa:
        assert len(mod._tai_khoan) == 1 and mod._tai_khoan[0].ho_so == HS


@pytest.mark.parametrize("email, token", [("", "x"), (EMAIL, ""), ("  ", " ")])
def test_thieu_du_lieu_thi_khong_lam_gi(mod, email, token):
    _gan(mod)
    kq = mod.gan_jwt_tu_dang_nhap(email, token, HS)
    assert kq == {"ok": False, "ma_loi": "thieu_du_lieu"}
    with mod._khoa:
        assert mod._tai_khoan == []


def test_email_khop_khong_phan_biet_hoa_thuong(mod):
    cu, moi = _jwt_gia(extra={"v": 5}), _jwt_gia(extra={"v": 6})
    _gan(mod, (cu, EMAIL, None))
    kq = mod.gan_jwt_tu_dang_nhap(EMAIL.upper(), moi, HS)
    assert kq["ok"] is True and kq["thay_the"] is True
    with mod._khoa:
        assert [t.token for t in mod._tai_khoan] == [moi]
