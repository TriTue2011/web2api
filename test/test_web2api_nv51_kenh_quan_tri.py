"""NV51 — test cho dấu vết trong kênh nhật ký quản trị.

Bối cảnh: báo cáo NV46 v2, mục 3(4) — lấp dấu vết còn lại: đổi địa chỉ/IP quản trị
(PUT /api/dia-chi) và duyệt phê duyệt (POST /api/phe-duyet/{id}/duyet) phải ghi vào
nhật ký quản trị (kênh «quan_tri»), không chỉ log cục bộ hay kênh khác.

Hiện trạng trên main b264902 (đã đo bằng chạy thật):
- PUT /api/dia-chi: KHÔNG ghi nhật ký ở đâu → test AT1 ĐỎ (xfail).
- POST /api/phe-duyet/{id}/duyet (thành công): chỉ ghi kênh «thiet_bi» → test AT2 ĐỎ (xfail).
- POST /api/nguoi-dung, PATCH /api/nguoi-dung/{id}, POST .../dat-lai-mat-khau,
  DELETE /api/nguoi-dung/{id}, POST /api/khoa-khach/{id}/xoay: đã ghi kênh
  «quan_tri» → test xanh phòng thủ.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import nguoi_dung, nhat_ky, thiet_bi
from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-qt-nv51-aaa-000000000000000000000000"
MK_ADMIN = "MatKhauAdminGia000000000000"
MK_USER = "MatKhauUserGia000000000000"

H_QT = {"Authorization": "Bearer " + KHOA_QT}

TINH_NANG_GIA = [
    {
        "hanh_dong": "hanh-dong-nguy-hiem",
        "mo_ta": "hành động giả cho kiểm thử",
        "rui_ro": "nguy_hiem",
    }
]


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


def _ban_ghi_quan_tri(q: str) -> list[dict]:
    return nhat_ky.doc(kenh="quan_tri", q=q)


class _ModGia:
    """Module giả: thực hiện hành động phê duyệt mà không cần module thật."""

    async def thuc_hien(self, thuc_the_id, hanh_dong, tham_so):
        return {"ok": True, "gia": True}


def _duyet_thanh_cong(client) -> str:
    """Dựng một mục phê duyệt và duyệt thành công nó; trả về pd_id.

    Dùng module giả + thực thể giả (mock lay_mot_thuc_the) vì test không có
    module thật nào đang chạy.
    """
    muc = thiet_bi.them_hang_cho_phe_duyet(
        ai_de_xuat="khoa-khach-gia",
        thuc_the="mod-gia.thuc-the-at2",
        hanh_dong="hanh-dong-nguy-hiem",
        tham_so={},
        tinh_nang=TINH_NANG_GIA,
    )
    so = client.app.state.so
    so.tim_theo_tien_to = lambda tien_to: "mod-gia"  # noqa: E731
    so.catalog["mod-gia"] = SimpleNamespace(tien_to="mod-gia")
    so.dang_chay["mod-gia"] = _ModGia()
    with patch(
        "app.thiet_bi.lay_mot_thuc_the",
        new=AsyncMock(
            return_value={"tinh_nang": TINH_NANG_GIA, "rui_ro": "nguy_hiem"}
        ),
    ):
        r = client.post(f"/api/phe-duyet/{muc['id']}/duyet", headers=H_QT)
    assert r.status_code == 200, f"duyệt giả phải 200, nhận {r.status_code}: {r.text[:200]}"
    return muc["id"]


# ─────────────────────────────────────────────────────────────────────────────
# DẤU VẾT CÒN THIẾU → test đỏ (xfail) cho tới khi được lấp
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="NV51 AT1: PUT /api/dia-chi chưa ghi vào kênh nhật ký quản trị",
)
def test_doi_dia_chi_ghi_kenh_quan_tri(moi_truong):
    client, _ = moi_truong
    r = client.put(
        "/api/dia-chi",
        json={"url": "https://dia-chi-gia-000.example.net:8443"},
        headers=H_QT,
    )
    assert r.status_code == 200
    recs = _ban_ghi_quan_tri("địa chỉ")
    assert len(recs) >= 1, (
        "đổi địa chỉ truy cập (thao tác đổi trạng thái của quản trị) phải để lại "
        "bản ghi trong kênh «quan_tri»"
    )


@pytest.mark.xfail(
    strict=True,
    reason="NV51 AT2: duyệt phê duyệt thành công chưa ghi vào kênh nhật ký quản trị",
)
def test_duyet_phe_duyet_ghi_kenh_quan_tri(moi_truong):
    client, _ = moi_truong
    _duyet_thanh_cong(client)
    recs = _ban_ghi_quan_tri("duyệt")
    assert len(recs) >= 1, (
        "duyệt phê duyệt (thao tác đổi trạng thái của quản trị) phải để lại "
        "bản ghi trong kênh «quan_tri», không chỉ kênh «thiet_bi»"
    )


# ─────────────────────────────────────────────────────────────────────────────
# DẤU VẾT ĐÃ ĐỦ → test xanh phòng thủ (đỏ nếu ai đó làm mất)
# ─────────────────────────────────────────────────────────────────────────────


def test_tao_nguoi_dung_ghi_kenh_quan_tri(moi_truong):
    client, _ = moi_truong
    r = client.post(
        "/api/nguoi-dung",
        json={
            "ten_dang_nhap": "user-at3",
            "mat_khau": MK_USER,
            "vai": "user",
        },
        headers=H_QT,
    )
    assert r.status_code == 200
    recs = _ban_ghi_quan_tri("tạo người dùng")
    assert len(recs) >= 1


def test_dat_lai_mat_khau_ghi_kenh_quan_tri(moi_truong):
    client, _ = moi_truong
    ban_ghi, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="user-at4", mat_khau=MK_USER
        )
    )
    r = client.post(
        f"/api/nguoi-dung/{ban_ghi['id']}/dat-lai-mat-khau", headers=H_QT
    )
    assert r.status_code == 200
    recs = _ban_ghi_quan_tri("đặt lại mật khẩu")
    assert len(recs) >= 1


def test_xoa_nguoi_dung_ghi_kenh_quan_tri(moi_truong):
    client, _ = moi_truong
    ban_ghi, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="user-at5", mat_khau=MK_USER
        )
    )
    r = client.delete(f"/api/nguoi-dung/{ban_ghi['id']}", headers=H_QT)
    assert r.status_code == 200
    recs = _ban_ghi_quan_tri("xoá tài khoản")
    assert len(recs) >= 1


def test_xoay_khoa_khach_ghi_kenh_quan_tri(moi_truong):
    client, _ = moi_truong
    r_tao = client.post(
        "/api/khoa-khach", json={"nhan": "khoa-gia-at6"}, headers=H_QT
    )
    assert r_tao.status_code == 200
    kid = r_tao.json()["khoa"]["id"]
    r = client.post(f"/api/khoa-khach/{kid}/xoay", json={}, headers=H_QT)
    assert r.status_code == 200
    recs = _ban_ghi_quan_tri("xoay")
    assert len(recs) >= 1


def test_tu_choi_phe_duyet_ghi_nhat_ky(moi_truong):
    """Từ chối phê duyệt cũng là quyết định của quản trị → phải có dấu vết
    (hiện ghi kênh «thiet_bi»; giữ nguyên hành vi, chỉ khẳng định không mất)."""
    client, _ = moi_truong
    muc = thiet_bi.them_hang_cho_phe_duyet(
        ai_de_xuat="khoa-khach-gia",
        thuc_the="mod-gia.thuc-the-at7",
        hanh_dong="hanh-dong-nguy-hiem",
        tham_so={},
    )
    r = client.post(f"/api/phe-duyet/{muc['id']}/tu-choi", headers=H_QT)
    assert r.status_code == 200
    recs = nhat_ky.doc(kenh="thiet_bi", q="từ chối")
    assert len(recs) >= 1
