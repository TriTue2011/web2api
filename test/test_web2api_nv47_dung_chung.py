"""NV47 — red team «lây nhiễm từ tài khoản khách sang admin»: mảng DÙNG CHUNG TRẠNG THÁI.

Khách không leo được quyền nhưng có thể làm hại admin qua tài nguyên dùng chung
(DoS / làm phiền). Test xfail(strict=True) lộ hành vi xấu; test xanh khẳng định
giới hạn đã có.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef-nv47b"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "3")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    with TestClient(app, raise_server_exceptions=False) as c:
        c.app.state.so = SoModule()
        yield c


def test_khoa_ip_chung_khoa_luon_admin(client, monkeypatch):
    """Kẻ tấn công và admin cùng IP (sau NAT/proxy): sai khoá nhiều → admin khoá đúng cũng 429."""
    from app import gioi_han
    monkeypatch.setattr(gioi_han.GioiHanSaiKhoa, "ip_hieu_luc", lambda self, h, x: "203.0.113.7")
    for _ in range(5):
        client.get("/v1/models", headers={"Authorization": "Bearer sai"})
    r = client.get("/v1/models", headers=QT)
    # mong muốn: admin dùng khoá ĐÚNG không bị ảnh hưởng bởi sai khoá của kẻ khác
    assert r.status_code == 200


def test_ke_do_khoa_nhieu_khoa_rieng_biet_van_bi_chan(client, monkeypatch):
    """Kẻ dò khoá (mỗi lần một khoá sai khác nhau) vẫn bị khoá IP — chống brute-force không nới lỏng (NV47)."""
    from app import gioi_han
    monkeypatch.setattr(gioi_han.GioiHanSaiKhoa, "ip_hieu_luc", lambda self, h, x: "203.0.113.7")
    for i in range(11):
        client.get("/v1/models", headers={"Authorization": f"Bearer sai-{i:03d}"})
    r = client.get("/v1/models", headers={"Authorization": "Bearer sai-xxx"})
    assert r.status_code == 429, r.text


def test_hang_cho_phe_duyet_co_tran(client, monkeypatch):
    """Khách (thiet_bi:ghi) spam hành động nguy hiểm → hàng chờ phình vô hạn."""
    from app import thiet_bi as thiet_bi_mod
    n0 = len(thiet_bi_mod.doc_hang_cho_phe_duyet())
    for i in range(300):
        thiet_bi_mod.them_hang_cho_phe_duyet(
            ai_de_xuat="khach:kk_spam", thuc_the="mock.den",
            hanh_dong="bat", tham_so={}, ly_do=f"spam {i}")
    n1 = len(thiet_bi_mod.doc_hang_cho_phe_duyet())
    # mong muốn: có trần (vd từ chối 429 sau N yêu cầu chờ) thay vì tăng vô hạn
    assert n1 - n0 < 300, "hàng chờ tăng vô hạn theo spam"


def test_tran_dong_thoi_toan_cuc_ap_dung_cho_ca_admin(monkeypatch):
    """Trần đồng thời /v1 áp dụng cho CẢ admin (không ai được miễn) — thiết kế đã đúng."""
    import os
    monkeypatch.setenv("WEB2API_V1_DONG_THOI_TOAN_CUC", "2")
    from app import khoa_khach
    khoa_khach._V1_DONG_THOI_HIEN_TAI = 0
    khoa_khach.vao_dong_thoi_toan_cuc()
    khoa_khach.vao_dong_thoi_toan_cuc()
    with pytest.raises(Exception):
        khoa_khach.vao_dong_thoi_toan_cuc()
    khoa_khach.ra_dong_thoi_toan_cuc()
    khoa_khach.ra_dong_thoi_toan_cuc()
