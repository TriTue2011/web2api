"""Tích hợp giữa hai tính năng: khoá API CÓ CHỦ (nguoi_dung) và khôi phục sao lưu (sao_luu).

`khoa_khach.kiem_tep` chuẩn hoá bản ghi khoá khi khôi phục; nếu nó làm rơi `chu_id` thì khoá của người dùng trở thành
khoá «không chủ» sau khôi phục — hết bị ràng buộc theo trần HIỆN TẠI của chủ và không còn tắt theo chủ."""

import pytest

from app import khoa_khach


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    khoa_khach.lam_moi_cache()
    yield tmp_path
    khoa_khach.lam_moi_cache()


def _ban_ghi_luu():
    khoa_khach.dong_bo_dia()
    return [dict(k) for k in khoa_khach._doc_tu_dia_neu_can()]


def test_kiem_tep_giu_chu_id_khi_khoi_phuc(du_lieu):
    khoa_khach.tao_khoa(nhan="cua-bob", pham_vi=["chat"], chu_id="bob123")
    khoa_khach.tao_khoa(nhan="khong-chu", pham_vi=["chat"])
    ds = _ban_ghi_luu()
    ra = khoa_khach.kiem_tep({"khoa": ds})
    theo_nhan = {k["nhan"]: k for k in ra}
    assert theo_nhan["cua-bob"].get("chu_id") == "bob123", "khôi phục làm rơi chu_id của khoá người dùng"
    assert "chu_id" not in theo_nhan["khong-chu"], "khoá không chủ không được tự có chủ"


@pytest.mark.parametrize("xau", [123, ["x"], {"a": 1}, "co khoang trang", "a" * 65, "../x", "id\n"])
def test_kiem_tep_tu_choi_chu_id_la(du_lieu, xau):
    khoa_khach.tao_khoa(nhan="x", pham_vi=["chat"], chu_id="bob123")
    ds = _ban_ghi_luu()
    ds[0]["chu_id"] = xau
    with pytest.raises(ValueError):
        khoa_khach.kiem_tep({"khoa": ds})


def test_tim_theo_bam_cung_cong_voi_tim_theo_khoa_tho(du_lieu):
    """Đường tra thật của xác thực (`tim_theo_bam`) và lớp bọc băm-rồi-tra (`tim_theo_khoa_tho`) cho cùng kết quả,
    để kiểm chứng sau khôi phục (dùng `tim_theo_bam`) không lệch với đường xác thực."""
    import hashlib
    _, tho = khoa_khach.tao_khoa(nhan="a", pham_vi=["chat"])
    bam = hashlib.sha256(tho.encode()).hexdigest()
    assert khoa_khach.tim_theo_bam(bam) == khoa_khach.tim_theo_khoa_tho(tho)
    assert khoa_khach.tim_theo_bam("0" * 64) is None
    assert khoa_khach.tim_theo_bam(None) is None
