"""Test NV271 — Antigravity: đường «đăng nhập» cho tài khoản Google đích «antigravity».

HTTP qua lõi thật với cầu nối THẬT (chạy cau_noi.app qua MayChuThat) và agy GIẢ (agy_gia.py).
Kiểm tra đầy đủ 7 ca G1 - G7:
- G1: tài khoản Google loại gmail lưu qua POST /api/tai-khoan/da-luu (email khớp HOME có phiên) ->
      POST /api/tai-khoan/da-luu/{id}/dang-nhap thân {"module_id":"antigravity"} -> thanh_cong: true
- G2: email chưa có HOME -> thanh_cong: false, can_nguoi: true, ma_loi: "chua_co_tai_khoan", huong_dan tiếng Việt
- G3: HOME có nhưng /usage hỏng -> chua_dang_nhap
- G4: cầu nối sai khoá -> thanh_cong: false, không ném, 200 từ route (ma_loi: "cau_noi_loi")
- G5: phản hồi HTTP và mọi nhật ký không chứa mật khẩu/khoá cầu nối/email của HOME khác
- G6: route thiếu khoá quản trị -> 401
- G7: tài khoản tạm dừng -> 403 và cầu nối 0 yêu cầu
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GOC = Path(__file__).resolve().parents[1]
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

DUONG_CAU_NOI = GOC / "dich-vu" / "antigravity-cau-noi"
if str(DUONG_CAU_NOI) not in sys.path:
    sys.path.insert(0, str(DUONG_CAU_NOI))

import cau_noi  # noqa: E402
from app.main import app as loi_app  # noqa: E402
from app.so_module import SoModule  # noqa: E402
from gia import MayChuThat  # noqa: E402

KHOA_QT = "khoa-quan-tri-test-nv271-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_CAU_NOI = "sk-lan-0000000000000000000000000000xyzw"

EMAIL_G1 = "nguoi.dung.g1@example.com"
EMAIL_G2 = "chua.co.home.g2@example.com"
EMAIL_G3 = "usage.hong.g3@example.com"
EMAIL_KHAC = "tai.khoan.khac@example.com"

MAT_KHAU_GIA = "mat-khau-gia-nv271-secret"


def _tao_home(duong: Path, email: str | None = None, **kw) -> Path:
    duong.mkdir(parents=True, exist_ok=True)
    (duong / "_agy_gia.json").write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
    if email:
        (duong / ".agy-cau-noi-email").write_text(email + "\n", encoding="utf-8")
    return duong


@pytest.fixture(autouse=True)
def _don_trang_thai_dang_nhap_google():
    """Dọn dẹp trạng thái toàn cục trước và sau mỗi test."""
    def _don():
        for m in list(sys.modules.values()):
            if m is not None and hasattr(m, "_LAN_DANG_NHAP_GOOGLE_THEO_TK"):
                try:
                    m._LAN_DANG_NHAP_GOOGLE_THEO_TK.clear()
                    m._LAN_DANG_NHAP_GOOGLE_XONG = 0.0
                    m._CAPTCHA_HOLD_DEN = 0.0
                    m._CAPTCHA_HOLD_HO_SO = ""
                except Exception:
                    pass
    _don()
    yield
    _don()


@pytest.fixture
def cau_noi_that(tmp_path, monkeypatch):
    """Khởi động cầu nối THẬT (cau_noi.app) qua MayChuThat trên loopback."""
    thu_muc_homes = tmp_path / "agy_homes"
    thu_muc_homes.mkdir()

    h0 = _tao_home(thu_muc_homes / "acc0", EMAIL_G1)
    h_hong = _tao_home(thu_muc_homes / "acc_hong", EMAIL_G3, usage_ma_thoat=1)
    h_khac = _tao_home(thu_muc_homes / "acc_khac", EMAIL_KHAC)

    monkeypatch.setenv("AGY_CAU_NOI_KHOA", KHOA_CAU_NOI)
    monkeypatch.setenv("AGY_BIN", str(DUONG_CAU_NOI / "agy_gia.py"))
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h_hong}:{h_khac}")
    monkeypatch.setattr(cau_noi, "be", None)

    with MayChuThat(cau_noi.app) as mc:
        mc.h0 = h0
        mc.h_hong = h_hong
        mc.h_khac = h_khac
        yield mc


@pytest.fixture
def moi_truong(tmp_path, monkeypatch, cau_noi_that):
    """Môi trường lõi web2api thật cài đặt module antigravity thật."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))

    # Chép module antigravity vào WEB2API_DATA/modules/antigravity
    shutil.copytree(
        GOC / "modules" / "antigravity",
        data_dir / "modules" / "antigravity",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    with TestClient(loi_app) as client:
        so = SoModule()
        client.app.state.so = so

        # Nạp lại module
        r_nap = client.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200, r_nap.text

        # Cài đặt cấu hình kết nối tới cầu nối
        r_cai = client.post(
            "/api/modules/antigravity/cai",
            headers=QT,
            json={
                "cau_hinh": {
                    "dia_chi_cau_noi": cau_noi_that.url,
                    "khoa_cau_noi": KHOA_CAU_NOI,
                }
            },
        )
        assert r_cai.status_code == 200, r_cai.text
        assert r_cai.json().get("ok") is True, r_cai.text

        yield client, so, cau_noi_that


def _luu_tai_khoan(client: TestClient, email: str, mat_khau: str = MAT_KHAU_GIA) -> str:
    """Lưu tài khoản Google loại gmail với đích dùng antigravity."""
    r = client.post(
        "/api/tai-khoan/da-luu",
        headers=QT,
        json={
            "email": email,
            "mat_khau": mat_khau,
            "module_id": "antigravity",
            "loai_tai_khoan": "gmail",
            "dich_dung": "antigravity",
            "nhan": f"Test {email}",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]["id"]


def test_g1_dang_nhap_thanh_cong_khi_home_co_phien(moi_truong):
    """G1: Tài khoản Google loại gmail khớp HOME có phiên -> dang-nhap trả thanh_cong: true."""
    client, so, _ = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G1)

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True
    assert kq["can_nguoi"] is False
    assert kq["cho_2fa"] is False
    assert kq["ma_loi"] == ""


def test_g2_chua_co_tai_khoan_can_nguoi_va_huong_dan(moi_truong):
    """G2: Email chưa có HOME -> thanh_cong: false, can_nguoi: true, ma_loi: 'chua_co_tai_khoan', huong_dan tiếng Việt."""
    client, so, _ = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G2)

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is False
    assert kq["can_nguoi"] is True
    assert kq["ma_loi"] == "chua_co_tai_khoan"
    assert "agy" in kq["huong_dan"] or "HOME" in kq["huong_dan"]
    assert "Kiểm tra lại" in kq["huong_dan"]


def test_g3_chua_dang_nhap_khi_usage_hong(moi_truong):
    """G3: HOME có nhưng /usage hỏng -> thanh_cong: false, can_nguoi: true, ma_loi: 'chua_dang_nhap'."""
    client, so, _ = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G3)

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is False
    assert kq["can_nguoi"] is True
    assert kq["ma_loi"] == "chua_dang_nhap"
    assert len(kq.get("huong_dan", "")) > 10


def test_g4_cau_noi_sai_khoa_tra_200_thanh_cong_false(moi_truong):
    """G4: Cầu nối sai khoá -> thanh_cong: false, ma_loi: 'cau_noi_loi', không ném, 200 từ route."""
    client, so, _ = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G1)

    # Đổi khoá cấu hình của module đang chạy thành sai
    mod = so.dang_chay["antigravity"]
    mod._khoa = "khoa-cau-noi-hoan-toan-sai"

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is False
    assert kq["ma_loi"] == "cau_noi_loi"


def test_g5_khong_lo_mat_khau_khoa_email_home_khac(moi_truong, caplog):
    """G5: Phản hồi HTTP và nhật ký không chứa mật khẩu, khoá cầu nối hay email của HOME khác."""
    client, so, _ = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G1, mat_khau=MAT_KHAU_GIA)

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 200, r.text

    # Không lộ mật khẩu, khoá cầu nối, email của HOME khác
    phan_hoi = r.text
    assert MAT_KHAU_GIA not in phan_hoi
    assert KHOA_CAU_NOI not in phan_hoi
    assert EMAIL_KHAC not in phan_hoi

    nhat_ky_log = caplog.text
    assert MAT_KHAU_GIA not in nhat_ky_log
    assert KHOA_CAU_NOI not in nhat_ky_log
    assert EMAIL_KHAC not in nhat_ky_log


def test_g6_thieu_khoa_quan_tri_tra_401(moi_truong):
    """G6: Tuyến /da-luu/{id}/dang-nhap thiếu khoá quản trị -> 401."""
    client, so, _ = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G1)

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 401

    # Khoá sai
    r_sai = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers={"Authorization": "Bearer khoa-quan-tri-sai-0123456789"},
        json={"module_id": "antigravity"},
    )
    assert r_sai.status_code == 401


def test_g7_tai_khoan_tam_dung_tra_403_va_cau_noi_0_yeu_cau(moi_truong):
    """G7: Tài khoản tạm dừng -> 403 và cầu nối 0 yêu cầu."""
    client, so, cau_noi_that = moi_truong
    tk_id = _luu_tai_khoan(client, EMAIL_G1)

    # Đặt tài khoản tạm dừng
    r_td = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/tam-dung",
        headers=QT,
        json={"tam_dung": True},
    )
    assert r_td.status_code == 200, r_td.text

    # Đo số lần agy chạy trong h0 trước khi gọi
    nhat_ky_h0_truoc = len((cau_noi_that.h0 / "_agy_gia_nhat_ky.jsonl").read_text().splitlines()) if (cau_noi_that.h0 / "_agy_gia_nhat_ky.jsonl").exists() else 0

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "tam_dung"

    # Cầu nối không bị gọi
    nhat_ky_h0_sau = len((cau_noi_that.h0 / "_agy_gia_nhat_ky.jsonl").read_text().splitlines()) if (cau_noi_that.h0 / "_agy_gia_nhat_ky.jsonl").exists() else 0
    assert nhat_ky_h0_sau == nhat_ky_h0_truoc, "Cầu nối đã bị gọi khi tài khoản tạm dừng"


def test_g8_idor_tai_khoan_khong_ton_tai_404(moi_truong):
    """G8 (IDOR / kiểm tra ID): ID tài khoản không tồn tại -> 404 khong_tim_thay."""
    client, so, _ = moi_truong
    r = client.post(
        "/api/tai-khoan/da-luu/id-hoan-toan-khong-ton-tai-12345/dang-nhap",
        headers=QT,
        json={"module_id": "antigravity"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "khong_tim_thay"

