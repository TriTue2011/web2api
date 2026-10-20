"""NV197e — nối «nhập tài khoản» (nhaptaikhoan.ap_dung) vào KHO tài khoản.

RED-FIRST: các test này ĐỎ trên cây chưa nối kho (ap_dung từ chối
«chua_noi_kho_tai_khoan»), XANH sau khi nối. Dữ liệu nguồn GIẢ, không mạng thật.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

_KHOA_QT = "khoa-qt-nv197e-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}

_BI_MAT_1 = "tok-gia-nhap-1-SECRET-khong-duoc-lo"
_BI_MAT_2 = "tok-gia-nhap-2-SECRET-khong-duoc-lo"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    """Lõi THẬT: app đầy đủ, dữ liệu kho cô lập trong tmp."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


def _cai_nhaptaikhoan(so):
    """Cài module nhaptaikhoan THẬT vào lõi (catalog + dang_chay)."""
    from types import SimpleNamespace

    from modules.nhaptaikhoan.module import Module

    manifest = json.loads(pathlib.Path("modules/nhaptaikhoan/manifest.json")
                          .read_text(encoding="utf-8"))
    mod = Module(manifest)
    so.catalog["nhaptaikhoan"] = SimpleNamespace(tien_to="nhaptaikhoan")
    so.dang_chay["nhaptaikhoan"] = mod
    return mod


def _ctx_qt():
    from app import ngu_canh
    return ngu_canh.dat({"loai": "quan_tri", "id": "qt-test",
                         "pham_vi": ["*"]}, nguon="nguoi")


def _goi_ap_dung(mod, danh_sach):
    from app import ngu_canh
    tok = _ctx_qt()
    try:
        return asyncio.run(mod.thuc_hien("nhaptaikhoan.ap_dung", "ap_dung",
                                         {"danh_sach": danh_sach}))
    finally:
        ngu_canh.khoi_phuc(tok)


def _muc(nguon="sub2api", dinh_danh="tk-gia-1", bi_mat=_BI_MAT_1,
         email="nhap-gia@example.com"):
    return {"nguon": nguon, "dinh_danh": dinh_danh, "bi_mat": bi_mat,
            "email": email}


# ── ghi thật vào kho ─────────────────────────────────────────────────────

def test_ap_dung_ghi_that_vao_kho(moi_truong):
    """Ô đỏ #12 NV197a: ap_dung ghi thật từng tài khoản vào kho lõi."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)

    kq = _goi_ap_dung(mod, [_muc(), _muc(dinh_danh="tk-gia-2",
                                        bi_mat=_BI_MAT_2,
                                        email="nhap2-gia@example.com")])
    assert kq["ok"] is True, kq
    assert kq["da_them"] == 2 and kq["trung"] == 0

    from app import kho_tai_khoan
    du_lieu = kho_tai_khoan._doc()
    id1 = kho_tai_khoan.ma_tai_khoan_nhap("sub2api", "tk-gia-1")
    assert du_lieu[id1]["bi_mat"] == _BI_MAT_1
    assert du_lieu[id1]["loai"] == "nhap"


def test_ap_dung_khu_trung(moi_truong):
    """Nhập lại cùng định danh → «trung», không ghi đè, kho chỉ 1 bản ghi."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)

    kq1 = _goi_ap_dung(mod, [_muc()])
    assert kq1["da_them"] == 1
    kq2 = _goi_ap_dung(mod, [_muc(bi_mat="bi-mat-moi-khac")])
    assert kq2["ok"] is True and kq2["trung"] == 1 and kq2["da_them"] == 0

    from app import kho_tai_khoan
    id1 = kho_tai_khoan.ma_tai_khoan_nhap("sub2api", "tk-gia-1")
    assert kho_tai_khoan._doc()[id1]["bi_mat"] == _BI_MAT_1  # không bị ghi đè


def test_ap_dung_khac_nguon_khong_de_len_nhau(moi_truong):
    """Cùng định danh nhưng khác nguồn → hai bản ghi riêng (không IDOR chéo)."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)

    kq = _goi_ap_dung(mod, [_muc(nguon="sub2api"), _muc(nguon="cpa")])
    assert kq["da_them"] == 2, kq

    from app import kho_tai_khoan
    assert len(kho_tai_khoan._doc()) == 2


def test_bao_cao_tung_dong_khong_lo_bi_mat(moi_truong):
    """Báo cáo từng dòng: đã thêm/trùng/bỏ qua/lỗi — KHÔNG lộ bí mật."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)

    kq = _goi_ap_dung(mod, [_muc(), _muc(dinh_danh="tk-trung"),
                            {"nguon": "sub2api", "dinh_danh": "tk-bo-qua",
                             "bi_mat": ""}])
    kq2 = _goi_ap_dung(mod, [_muc(), {"nguon": "sub2api",
                                     "dinh_danh": "tk-trung",
                                     "bi_mat": "x"}])
    dong = {d["khoa_che"]: d["ket_qua"] for d in kq2["chi_tiet"]}
    assert kq["chi_tiet"][0]["ket_qua"] == "da_them"
    assert kq["chi_tiet"][2]["ket_qua"] == "bo_qua"
    assert dong["tk-trung"] == "trung"

    tho = json.dumps(kq) + json.dumps(kq2)
    assert _BI_MAT_1 not in tho and _BI_MAT_2 not in tho
    assert "nhap-gia@example.com" not in tho  # email thô cũng không lộ


# ── xác thực / phân quyền ────────────────────────────────────────────────

def test_khong_quan_tri_bi_tu_choi(moi_truong):
    """Không ngữ cảnh quản trị → từ chối (401)."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    kq = asyncio.run(mod.thuc_hien("nhaptaikhoan.ap_dung", "ap_dung",
                                   {"danh_sach": [_muc()]}))
    assert kq == {"ok": False, "loi": "chi_quan_tri"}


def test_goi_ap_dung_qua_thuc_the_xem_truoc_bi_tu_choi(moi_truong):
    """Gọi hành động nguy hiểm qua thực thể rủi ro thấp → từ chối (403)."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    from app import ngu_canh
    tok = _ctx_qt()
    try:
        kq = asyncio.run(mod.thuc_hien("nhaptaikhoan.xem_truoc", "ap_dung",
                                       {"danh_sach": [_muc()]}))
    finally:
        ngu_canh.khoi_phuc(tok)
    assert kq == {"ok": False, "loi": "hanh_dong_khong_ho_tro"}


def test_qua_loi_that_http(moi_truong):
    """QUA LÕI THẬT: HTTP cần Bearer <redacted>ản trị + xac_nhan (step-up miễn).

    Thiếu xac_nhan → 409; sai khoá → 401; đủ điều kiện → 200 và ghi thật.
    """
    client, so = moi_truong
    _cai_nhaptaikhoan(so)
    url = "/api/thiet-bi/nhaptaikhoan.ap_dung/hanh-dong"
    than = {"hanh_dong": "ap_dung",
            "tham_so": {"danh_sach": [_muc(dinh_danh="tk-http")]}}

    r = client.post(url, json=than)  # không khoá
    assert r.status_code == 401, r.text
    r = client.post(url, headers={"Authorization": "Bearer <redacted>"}, json=than)
    assert r.status_code == 401, r.text
    r = client.post(url, headers=_QT, json=than)  # thiếu xac_nhan
    assert r.status_code == 409, r.text

    r = client.post(url, headers=_QT, json={**than, "xac_nhan": True})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    from app import kho_tai_khoan
    id1 = kho_tai_khoan.ma_tai_khoan_nhap("sub2api", "tk-http")
    assert kho_tai_khoan._doc()[id1]["bi_mat"] == _BI_MAT_1


# ── giới hạn / kiểm dữ liệu không tin ────────────────────────────────────

def test_gioi_han_so_luong(moi_truong):
    """Quá 20 mục/lần → từ chối cả lô."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    ds = [_muc(dinh_danh=f"tk-{i}") for i in range(21)]
    kq = _goi_ap_dung(mod, ds)
    assert kq == {"ok": False, "loi": "danh_sach_qua_lon"}


def test_bi_mat_qua_lon_bi_loai_tung_dong(moi_truong):
    """Bí mật > 4096 ký tự → dòng đó «loi», các dòng khác vẫn ghi."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    kq = _goi_ap_dung(mod, [_muc(), _muc(dinh_danh="tk-lon", bi_mat="x" * 5000)])
    assert kq["ok"] is True
    dong = {d["khoa_che"]: d["ket_qua"] for d in kq["chi_tiet"]}
    assert dong["tk-gia-1"] == "da_them" and dong["tk-lon"] == "loi"


def test_nguon_la_bi_tu_choi(moi_truong):
    """Nguồn ngoài danh sách đóng → «loi» (không danh sách từ khoá)."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    kq = _goi_ap_dung(mod, [_muc(nguon="evil-source")])
    assert kq["ok"] is True and kq["chi_tiet"][0]["ket_qua"] == "loi"


def test_dinh_danh_doc_hai_bi_loai(moi_truong):
    """Định danh chứa ký tự điều khiển → «loi»."""
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    kq = _goi_ap_dung(mod, [_muc(dinh_danh="tk\x00-doc")])
    assert kq["chi_tiet"][0]["ket_qua"] == "loi"


def test_nguyen_tu_khi_kho_loi(moi_truong, monkeypatch):
    """Lỗi ghi giữa chừng → KHÔNG để kho nửa vời (một lần ghi nguyên tử).

    Giả lập đĩa hỏng khi ghi lô đủ 2 mục: mã ghi từng mục sẽ để lại mục 1
    đã ghi (nửa vời) rồi mới hỏng ở mục 2 — mã đúng chỉ ghi MỘT lần nên
    kho vẫn rỗng và cả lô báo «loi».
    """
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)

    from app import kho_tai_khoan
    goi = {"n": 0}

    # Ghi thật gốc để lần ghi 1 mục (mã đột biến ghi từng mục) đi qua:
    _ghi_goc = kho_tai_khoan._ghi

    def _ghi_hong2(du_lieu):
        goi["n"] += 1
        if len(du_lieu) >= 2:
            raise RuntimeError("o-dia-gia-hong-day")
        _ghi_goc(du_lieu)

    monkeypatch.setattr(kho_tai_khoan, "_ghi", _ghi_hong2)
    kq = _goi_ap_dung(mod, [_muc(), _muc(dinh_danh="tk-2", bi_mat=_BI_MAT_2)])
    assert kq["ok"] is True
    assert all(d["ket_qua"] == "loi" for d in kq["chi_tiet"])
    assert goi["n"] == 1  # chỉ một lần ghi → không ghi dở từng mục
    assert kho_tai_khoan._doc() == {}


def test_log_khong_chua_bi_mat(moi_truong, caplog):
    """Nhật ký trong lúc áp dụng không chứa bí mật thô."""
    import logging
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    with caplog.at_level(logging.DEBUG):
        _goi_ap_dung(mod, [_muc()])
    assert _BI_MAT_1 not in caplog.text


def test_xem_truoc_khong_doi(moi_truong):
    """Hồi quy: xem trước tệp 9router vẫn chạy như cũ."""
    import base64
    _client, so = moi_truong
    mod = _cai_nhaptaikhoan(so)
    data = {"providerConnections": [
        {"provider": "openai", "connectionName": "tk-a",
         "accessToken": "eyJnaWE", "email": "a@example.com"}]}
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    from app import ngu_canh
    tok = _ctx_qt()
    try:
        kq = asyncio.run(mod.thuc_hien(
            "nhaptaikhoan.xem_truoc", "xem_truoc_tep",
            {"dang": "ninerouter", "noi_dung_base64": b64}))
    finally:
        ngu_canh.khoi_phuc(tok)
    assert kq["ok"] is True and kq["co_token"] == 1
    assert "eyJnaWE" not in json.dumps(kq)
