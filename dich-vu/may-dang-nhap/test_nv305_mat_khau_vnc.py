# test_nv305_mat_khau_vnc.py — Mật khẩu xem trình duyệt (VNC) do chủ máy tự đặt (NV305).
#
# Chỉ test mô-đun thuần `src.mat_khau_vnc` (không import app FastAPI nặng):
# kiểm định dạng, ghi tệp nguyên tử 0600, tệp đánh dấu «đã đặt», nạp lại x11vnc
# qua supervisorctl, và không để mật khẩu lọt vào log. Mật khẩu trong test đều tự
# tạo, không phải mật khẩu thật.
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from src.mat_khau_vnc import (
    TEN_TEP_DANH_DAU,
    TEN_TEP_MAT_KHAU,
    ghi_mat_khau_vnc,
    kiem_mat_khau_vnc,
    trang_thai_da_dat,
)

MK = "Nv305-T1"  # 8 ký tự test, không phải mật khẩu thật


class ChayGia:
    """Giả supervisorctl: ghi lại lệnh, trả mã thoát đã định."""

    def __init__(self, ma=0, loi=None):
        self.ma = ma
        self.loi = loi
        self.lenh = []

    def __call__(self, lenh, **kwargs):
        self.lenh.append(list(lenh))
        if self.loi is not None:
            raise self.loi
        return self.ma


@pytest.mark.parametrize("mk", ["a", "Ab3!x9zQ", "12345678", "z"])
def test_mat_khau_hop_le(mk):
    assert kiem_mat_khau_vnc(mk) is None


@pytest.mark.parametrize("mk", ["", "123456789", "ab cd", "mậtkhẩu1", "ab\ncd", "ab\tcd", 123, None, "a b"])
def test_mat_khau_sai_dinh_dang(mk):
    assert kiem_mat_khau_vnc(mk) is not None


def test_ghi_tep_0600_va_danh_dau_va_nap_lai(tmp_path):
    chay = ChayGia(ma=0)
    kq = ghi_mat_khau_vnc(MK, thu_muc_khoa=tmp_path, chay_lenh=chay)
    assert kq == {"ok": True, "da_nap_lai": True}
    tep = tmp_path / TEN_TEP_MAT_KHAU
    assert tep.read_text(encoding="utf-8") == MK
    assert stat.S_IMODE(tep.stat().st_mode) == 0o600
    danh_dau = tmp_path / TEN_TEP_DANH_DAU
    assert danh_dau.exists()
    assert stat.S_IMODE(danh_dau.stat().st_mode) == 0o600
    assert trang_thai_da_dat(tmp_path) is True
    assert chay.lenh and chay.lenh[0][0] == "supervisorctl" and "x11vnc" in chay.lenh[0]
    assert "restart" in chay.lenh[0]


def test_ghi_de_thay_noi_dung_cu(tmp_path):
    (tmp_path / TEN_TEP_MAT_KHAU).write_text("mat-khau-cu-000", encoding="utf-8")
    ghi_mat_khau_vnc(MK, thu_muc_khoa=tmp_path, chay_lenh=ChayGia(ma=0))
    assert (tmp_path / TEN_TEP_MAT_KHAU).read_text(encoding="utf-8") == MK


def test_nap_lai_hong_van_ghi_tep_nhung_bao_chua_nap_lai(tmp_path):
    kq = ghi_mat_khau_vnc(MK, thu_muc_khoa=tmp_path, chay_lenh=ChayGia(ma=1))
    assert kq == {"ok": True, "da_nap_lai": False}
    assert (tmp_path / TEN_TEP_MAT_KHAU).read_text(encoding="utf-8") == MK
    kq2 = ghi_mat_khau_vnc(MK, thu_muc_khoa=tmp_path,
                           chay_lenh=ChayGia(loi=FileNotFoundError("khong co supervisorctl")))
    assert kq2 == {"ok": True, "da_nap_lai": False}


def test_chua_dat_thi_trang_thai_false(tmp_path):
    assert trang_thai_da_dat(tmp_path) is False


def test_mat_khau_sai_khong_ghi_tep(tmp_path):
    with pytest.raises(ValueError):
        ghi_mat_khau_vnc("qua-dai-123456", thu_muc_khoa=tmp_path, chay_lenh=ChayGia(ma=0))
    assert not (tmp_path / TEN_TEP_MAT_KHAU).exists()


def test_log_khong_chua_mat_khau(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="may-dang-nhap.mat-khau-vnc"):
        ghi_mat_khau_vnc(MK, thu_muc_khoa=tmp_path, chay_lenh=ChayGia(ma=0))
    assert all(MK not in (rec.getMessage()) for rec in caplog.records)


def test_main_co_duong_mat_khau_vnc():
    """Đấu nối tĩnh: main.py phải có đủ hai đường (PUT đặt + GET trạng thái), gắn nhóm dang_nhap."""
    src = (Path(__file__).resolve().parent / "src" / "main.py").read_text(encoding="utf-8")
    assert '"/v1/novnc/mat-khau"' in src
    assert '"/v1/novnc/mat-khau/trang-thai"' in src
    assert 'can_tinh_nang("dang_nhap")' in src
