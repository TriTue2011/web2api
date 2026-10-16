"""Kiểm thử bộ kiểm biên và an toàn thứ tự ghi / chmod không nuốt lỗi (NV45).

Các ca kiểm:
- kiem_chuoi: từ chối surrogate (U+D800), NUL byte, kiểu sai (bool, list, dict, int), trần sàn độ dài.
- kiem_thanh_vien: kiểu unhashable (list, dict, set, nested) trả về 400 không 500; phần tử ngoài tập trả 400.
- kiem_so_thuc: từ chối NaN, Infinity, -Infinity, bool, kiểu sai.
- kiem_so_nguyen: từ chối bool, 10**400 vượt trần, kiểu sai.
- kiem_boolean, kiem_danh_sach, kiem_tu_dien: kiểm tra kiểu chặt chẽ.
- ghi_bi_mat_tep: chmod 0700 thất bại -> ném OSError, từ chối ghi (không nuốt lỗi).
- thứ tự ghi: monkeypatch ghi đĩa ném -> bộ nhớ cache không thay đổi.
"""

import math
from pathlib import Path
import pytest

from app import kiem_bien, ham_khoa, kho, khoa_khach
from app.loi_openai import LoiOpenAI


# ─────────────────── kiem_chuoi ───────────────────

def test_kiem_chuoi_surrogate_bi_tu_choi():
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_chuoi("chua_surrogate_\ud800_loi", ten_truong="nhan")
    assert exc_info.value.ma == 400
    assert "surrogate" in exc_info.value.than["error"]["message"].lower()


def test_kiem_chuoi_nul_byte_bi_tu_choi():
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_chuoi("chuoi\x00co_nul", ten_truong="ten", cam_nul=True)
    assert exc_info.value.ma == 400


@pytest.mark.parametrize("kieu_sai", [True, False, 123, 4.56, [], {}, object()])
def test_kiem_chuoi_kieu_sai_bi_tu_choi(kieu_sai):
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_chuoi(kieu_sai, ten_truong="chuoi_test")
    assert exc_info.value.ma == 400


def test_kiem_chuoi_do_dai_tran_san():
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_chuoi("ngan", toi_thieu=10)
    assert exc_info.value.ma == 400

    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_chuoi("qua_dai_hon_muc_cho_phep", toi_da=5)
    assert exc_info.value.ma == 400

    assert kiem_bien.kiem_chuoi("hop_le", toi_thieu=2, toi_da=10) == "hop_le"


# ─────────────────── kiem_thanh_vien ───────────────────

@pytest.mark.parametrize("unhashable", [[], {}, [1, 2], {"a": 1}, set(), [[1]], [{"x": 1}]])
def test_kiem_thanh_vien_unhashable_tra_400_khong_500(unhashable):
    tap_hop = {"doc", "ghi", "nguy_hiem"}
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_thanh_vien(unhashable, tap_hop, ten_truong="rui_ro")
    assert exc_info.value.ma == 400


def test_kiem_thanh_vien_khong_co_trong_tap():
    tap_hop = {"doc", "ghi", "nguy_hiem"}
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_thanh_vien("khong_hop_le", tap_hop, ten_truong="rui_ro")
    assert exc_info.value.ma == 400


def test_kiem_thanh_vien_hop_le():
    tap_hop = {"doc", "ghi", "nguy_hiem"}
    assert kiem_bien.kiem_thanh_vien("doc", tap_hop) == "doc"


# ─────────────────── kiem_so_thuc ───────────────────

@pytest.mark.parametrize("doc", [float("nan"), float("inf"), float("-inf")])
def test_kiem_so_thuc_tu_choi_nan_infinity(doc):
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_so_thuc(doc, ten_truong="diem")
    assert exc_info.value.ma == 400


@pytest.mark.parametrize("kieu_sai", [True, False, "1.5", [], {}])
def test_kiem_so_thuc_kieu_sai(kieu_sai):
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_so_thuc(kieu_sai, ten_truong="so_thuc")
    assert exc_info.value.ma == 400


def test_kiem_so_thuc_tran_san():
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_so_thuc(10.5, toi_da=10.0)
    assert exc_info.value.ma == 400

    assert kiem_bien.kiem_so_thuc(5.5, toi_thieu=0.0, toi_da=10.0) == 5.5


# ─────────────────── kiem_so_nguyen ───────────────────

@pytest.mark.parametrize("kieu_sai", [True, False, 1.5, "123", [], {}])
def test_kiem_so_nguyen_kieu_sai(kieu_sai):
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_so_nguyen(kieu_sai, ten_truong="so_nguyen")
    assert exc_info.value.ma == 400


def test_kiem_so_nguyen_10_luy_thua_400():
    lon = 10 ** 400
    with pytest.raises(LoiOpenAI) as exc_info:
        kiem_bien.kiem_so_nguyen(lon, toi_da=1_000_000, ten_truong="so")
    assert exc_info.value.ma == 400


# ─────────────────── kiem_boolean, danh_sach, tu_dien ───────────────────

def test_kiem_boolean():
    assert kiem_bien.kiem_boolean(True) is True
    assert kiem_bien.kiem_boolean(False) is False
    for kieu_sai in [1, 0, "true", [], {}]:
        with pytest.raises(LoiOpenAI):
            kiem_bien.kiem_boolean(kieu_sai)


def test_kiem_danh_sach():
    assert kiem_bien.kiem_danh_sach([1, 2, 3], toi_da=5) == [1, 2, 3]
    for kieu_sai in ["abc", (1, 2), {}, 123]:
        with pytest.raises(LoiOpenAI):
            kiem_bien.kiem_danh_sach(kieu_sai)


def test_kiem_tu_dien():
    assert kiem_bien.kiem_tu_dien({"a": 1}) == {"a": 1}
    for kieu_sai in [[], "{}", 123]:
        with pytest.raises(LoiOpenAI):
            kiem_bien.kiem_tu_dien(kieu_sai)


# ─────────────────── ham_khoa: chmod thất bại ném lỗi ───────────────────

def test_ghi_bi_mat_tep_chmod_that_bai_nem_loi(tmp_path, monkeypatch):
    """Khi chmod 0700 thất bại, ghi_bi_mat_tep PHẢI ném lỗi thay vì nuốt âm thầm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-chinh-test-1234567890abcdef")
    p = tmp_path / "bi_mat" / "test_chmod" / "khoa.json"

    import os
    orig_chmod = os.chmod

    def _chmod_fail(path, mode):
        if "test_chmod" in str(path):
            raise PermissionError("Lỗi chmod thử nghiệm")
        return orig_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", _chmod_fail)
    with pytest.raises(PermissionError):
        ham_khoa.ghi_bi_mat_tep(p, {"bi_mat": "123"})


# ─────────────────── khoa_khach: thứ tự ghi ───────────────────

def test_khoa_khach_ghi_dia_loi_bo_nho_khong_doi(tmp_path, monkeypatch):
    """Ghi đĩa lỗi (monkeypatch ném) -> cache trong RAM không đổi, không nhiễm độc cho lần sau."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-chinh-test-1234567890abcdef")
    khoa_khach.lam_moi_cache()

    ban_ghi, _ = khoa_khach.tao_khoa(nhan="khoa_hop_le_1", pham_vi=["*"])
    so_luong_truoc = len(khoa_khach.danh_sach_khoa())

    def _ghi_loi(*args, **kwargs):
        raise OSError("Lỗi đĩa mô phỏng")

    with monkeypatch.context() as m:
        m.setattr(kho, "ghi_nguyen_tu", _ghi_loi)
        with pytest.raises(OSError):
            khoa_khach.tao_khoa(nhan="khoa_loi", pham_vi=["*"])

    # Bộ nhớ không đổi
    assert len(khoa_khach.danh_sach_khoa()) == so_luong_truoc

    # Lần ghi tiếp theo thành công bình thường
    ban_ghi2, _ = khoa_khach.tao_khoa(nhan="khoa_hop_le_2", pham_vi=["*"])
    assert ban_ghi2["nhan"] == "khoa_hop_le_2"
    assert len(khoa_khach.danh_sach_khoa()) == so_luong_truoc + 1
