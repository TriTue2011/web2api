"""Lỗi sở hữu khi đăng nhập UỶ QUYỀN qua module khác (đo thật 23/10/2026).

Tài khoản ChatGPT (bản ghi sở hữu ``chatgpt-web``) đăng nhập THÀNH CÔNG qua
module thực thi ``may-dang-nhap``. Sau lượt đó bản ghi bị ghi đè thành
``module_id = may-dang-nhap``, ``nhom = may-dang-nhap`` — vì route đăng nhập
gọi ``dat_module(id, module_id_cua_REQUEST, ...)`` vô điều kiện khi thành
công (NV197c «học module sở hữu» dùng module THỰC THI thay vì chỉ học khi
chưa có sở hữu). Lần đăng nhập sau, lõi đọc sở hữu sai → module đăng nhập
chọn sai luồng (ChatGPT rơi xuống đường Google) → hỏng hẳn.

Hai khẳng định:
  1. Đăng nhập uỷ quyền thành công KHÔNG đổi module sở hữu / nhóm đã có.
  2. NV197c vẫn giữ nguyên ý: tài khoản CHƯA có sở hữu thì học module thực
     thi làm sở hữu sau lần đăng nhập thành công.
Harness theo khuôn test_nv210_noi_jwt_vao_khoa.py (lõi thật + module giả).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-so-huu-uy-quyen-0123456789"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
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


def _cai(so, module_id, doi_tuong):
    man = getattr(doi_tuong, "manifest", None) or {"id": module_id}
    so.catalog[module_id] = SimpleNamespace(tien_to=module_id, manifest=man)
    so.dang_chay[module_id] = doi_tuong


def _module(id_, nhom_id):
    return SimpleNamespace(
        manifest={"id": id_, "nhom": {"id": nhom_id, "ten": nhom_id}})


def _executor(thanh_cong=True):
    async def dang_nhap(dong):
        return SimpleNamespace(
            thanh_cong=thanh_cong, can_nguoi=False, cho_2fa=False,
            huong_dan="",
            ma_loi="" if thanh_cong else "dang_nhap_that_bai",
            jwt_moi=None)

    ex = _module("ex-gia", "nhom-ex")
    ex.dang_nhap = dang_nhap
    return ex


def _luu(client, email, module_id="so-huu-gia"):
    than = {"email": email, "mat_khau": "MatKhauGia123!"}
    if module_id is not None:
        than["module_id"] = module_id
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=than)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]["id"]


def _ban_ghi(tk_id):
    from app import kho_tai_khoan as k

    return k.tim_theo_id(tk_id)


def _dang_nhap(client, tk_id):
    return client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=_QT, json={"module_id": "ex-gia"})


def test_dang_nhap_uy_quyen_khong_ghi_de_module_so_huu(moi_truong):
    """Sở hữu đã có (so-huu-gia) + thực thi khác (ex-gia) → giữ nguyên."""
    client, so = moi_truong
    _cai(so, "so-huu-gia", _module("so-huu-gia", "nhom-gia"))
    _cai(so, "ex-gia", _executor())
    tk = _luu(client, "nguoi-so-huu@example.com")
    truoc = _ban_ghi(tk)
    assert truoc["module_id"] == "so-huu-gia"
    r = _dang_nhap(client, tk)
    assert r.status_code == 200 and r.json()["thanh_cong"] is True
    sau = _ban_ghi(tk)
    assert sau["module_id"] == "so-huu-gia", (
        f"sở hữu bị ghi đè thành {sau['module_id']!r} sau đăng nhập uỷ quyền")
    assert sau["nhom"] == truoc["nhom"]


def test_dang_nhap_hoc_module_so_huu_khi_chua_co(moi_truong):
    """Chưa có sở hữu → NV197c: học module thực thi làm sở hữu."""
    client, so = moi_truong
    _cai(so, "ex-gia", _executor())
    tk = _luu(client, "nguoi-chua-so-huu@example.com", module_id=None)
    truoc = _ban_ghi(tk)
    assert not (truoc.get("module_id") or "")
    r = _dang_nhap(client, tk)
    assert r.status_code == 200 and r.json()["thanh_cong"] is True
    sau = _ban_ghi(tk)
    assert sau["module_id"] == "ex-gia"
    assert sau["nhom"] == "nhom-ex"


def test_dang_nhap_that_bai_khong_doi_so_huu(moi_truong):
    """Đăng nhập hỏng → không học, không ghi đè gì hết."""
    client, so = moi_truong
    _cai(so, "so-huu-gia", _module("so-huu-gia", "nhom-gia"))
    _cai(so, "ex-gia", _executor(thanh_cong=False))
    tk = _luu(client, "nguoi-that-bai@example.com")
    r = _dang_nhap(client, tk)
    assert r.status_code == 200 and r.json()["thanh_cong"] is False
    sau = _ban_ghi(tk)
    assert sau["module_id"] == "so-huu-gia"
