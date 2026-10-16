"""NV19 — lấp lỗ hổng NV14 nhóm sao_luu (CHỈ THÊM TEST, không sửa mã).

11. app/sao_luu.py:la_tep_cho_phep luôn True → tệp ngoài danh sách cho phép bị từ chối.
12. app/sao_luu.py:_kiem_doi_tuong chấp nhận mọi kiểu → bí mật không phải dict bị từ chối.
"""

from __future__ import annotations

import pytest

from app import sao_luu


@pytest.mark.parametrize("duong", [
    "../ngoai.json",
    "a/../../ngoai.json",
    "/tuyet-doi.json",
    "a\\b.json",
    "bi_mat/../ngoai.json",
    "x/../..",
    "",
])
def test_la_tep_cho_phep_tu_choi_ngoai_danh_sach(duong):
    """NV14#11: la_tep_cho_phep luôn True → các đường ngoài phạm vi phải bị từ chối."""
    assert sao_luu.la_tep_cho_phep(duong) is False


def test_la_tep_cho_phep_chap_nhan_hop_le():
    """Đối chứng: tệp trong phạm vi vẫn được chấp nhận."""
    assert sao_luu.la_tep_cho_phep("manifest.json") is True
    assert sao_luu.la_tep_cho_phep("model.json") is True


@pytest.mark.parametrize("gia_tri", [
    ["danh", "sach"],
    "chuoi",
    123,
    4.5,
    None,
    True,
])
def test_kiem_doi_tuong_tu_choi_khong_phai_dict(gia_tri):
    """NV14#12: _kiem_doi_tuong chấp nhận mọi kiểu → không phải dict phải bị từ chối."""
    with pytest.raises(ValueError, match="phải là đối tượng JSON"):
        sao_luu._kiem_doi_tuong(gia_tri)


def test_kiem_doi_tuong_chap_nhan_dict():
    """Đối chứng: dict vẫn được chấp nhận."""
    assert sao_luu._kiem_doi_tuong({"a": 1}) == {"a": 1}
