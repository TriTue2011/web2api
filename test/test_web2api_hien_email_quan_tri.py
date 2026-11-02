"""Giao diện quản trị phải phân biệt được các tài khoản cùng tên miền (chủ máy 02/11/2026: «web2api ẩn hết mail làm tôi chả biết mail nào để sử dụng»).

`che_email` chỉ chừa tên miền, nên mọi tài khoản Gmail đều là «…***@gmail.com». Mặc định VẪN che (MCP, nhật ký, mọi đường khác đi qua `liet_ke()`).
Chỉ `GET /api/tai-khoan/da-luu?hien_email=true` của QUẢN TRỊ trả thêm trường `email` đầy đủ."""

from __future__ import annotations

import pytest

_KHOA_QT = "khoa-qt-hien-email-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
EMAIL_A = "nguoi.dung.mot@example.com"
EMAIL_B = "nguoi.dung.hai@example.com"


@pytest.fixture()
def may(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    d = tmp_path / "data-loi"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(d))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        for e in (EMAIL_A, EMAIL_B):
            r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={"email": e, "mat_khau": "MatKhauGia123!"})
            assert r.status_code == 200, r.text
        yield client


def _ds(client, query="", headers=_QT):
    r = client.get("/api/tai-khoan/da-luu" + query, headers=headers)
    return r


def test_mac_dinh_van_che_va_khong_co_truong_email(may):
    r = _ds(may)
    assert r.status_code == 200
    for tk in r.json()["tai_khoan"]:
        assert "email" not in tk and tk["email_che"].endswith("@example.com")
    assert EMAIL_A not in r.text and EMAIL_B not in r.text


def test_quan_tri_xin_hien_email_thi_co_email_day_du_phan_biet_duoc_tung_tai_khoan(may):
    r = _ds(may, "?hien_email=true")
    assert r.status_code == 200
    emails = sorted(tk["email"] for tk in r.json()["tai_khoan"])
    assert emails == sorted([EMAIL_A, EMAIL_B])
    assert all(tk["email_che"].endswith("@example.com") for tk in r.json()["tai_khoan"])        # trường che vẫn giữ nguyên


@pytest.mark.parametrize("gia_tri", ["false", "0", "", "khong", "no"])
def test_gia_tri_khac_true_thi_van_che(may, gia_tri):
    r = _ds(may, f"?hien_email={gia_tri}")
    assert r.status_code in (200, 422)
    assert EMAIL_A not in r.text and EMAIL_B not in r.text


def test_khong_phai_quan_tri_khong_lay_duoc_email_du_co_xin(may):
    for headers in ({}, {"Authorization": "Bearer khoa-sai-khong-ton-tai"}):
        r = _ds(may, "?hien_email=true", headers)
        assert r.status_code in (401, 403)
        assert EMAIL_A not in r.text and EMAIL_B not in r.text


def test_kho_khong_bi_doi_va_lan_goi_sau_khong_xin_thi_che_lai(may):
    _ds(may, "?hien_email=true")
    r = _ds(may)
    assert EMAIL_A not in r.text            # không có trạng thái «đã hiện» nào dính lại giữa các lần gọi


def test_email_trong_phan_hoi_khong_the_chen_html(may):
    # email đã qua bộ kiểm hẹp lúc lưu; ở biên ra vẫn escape như nhãn để giao diện chèn vào DOM an toàn
    from app import kho_tai_khoan as k
    assert k.ban_cong_khai({"id": "x", "email": "a&b@example.com", "nhan": ""}, hien_email=True)["email"] == "a&amp;b@example.com"


def _tat_ca_tai_khoan(nhom_loai):
    return [tk for loai in nhom_loai.values() for muc in loai.values() for tk in muc["tai_khoan"]]


def test_gom_theo_nhom_loai_mac_dinh_che_va_co_email_khi_quan_tri_xin(may):
    r = may.get("/api/tai-khoan/nhom-loai", headers=_QT)
    assert r.status_code == 200 and EMAIL_A not in r.text
    assert all("email" not in tk for tk in _tat_ca_tai_khoan(r.json()["nhom_loai"]))
    r2 = may.get("/api/tai-khoan/nhom-loai?hien_email=true", headers=_QT)
    assert sorted(tk["email"] for tk in _tat_ca_tai_khoan(r2.json()["nhom_loai"])) == sorted([EMAIL_A, EMAIL_B])
    r3 = may.get("/api/tai-khoan/nhom-loai?hien_email=true", headers={})
    assert r3.status_code in (401, 403) and EMAIL_A not in r3.text


def test_giao_dien_quan_tri_xin_email_o_ca_hai_danh_sach_va_hien_email_truoc_email_che():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "He-thong-AI.dc.html").read_text(encoding="utf-8")
    assert "api/tai-khoan/da-luu?hien_email=true" in html and "api/tai-khoan/nhom-loai?hien_email=true" in html
    import re
    assert "t.email || t.email_che" in html
    # mọi chỗ đọc `email_che` đều phải đứng sau «<biến>.email ||»: không còn chỗ nào hiện thẳng bản che
    assert not re.findall(r"(?<!email \|\| )\b(?:t|tk)\.email_che", html)


def test_ham_lieu_ke_va_gom_mac_dinh_khong_bao_gio_co_email_cho_cac_duong_khac(may):
    """MCP, nhật ký và mọi nơi gọi thẳng kho đều dùng mặc định: phải che."""
    from app import kho_tai_khoan as k
    assert k.liet_ke() and all("email" not in tk for tk in k.liet_ke())
    assert all("email" not in tk for tk in _tat_ca_tai_khoan(k.gom_theo_nhom_loai()))
    assert all("email" in tk for tk in k.liet_ke(hien_email=True))
