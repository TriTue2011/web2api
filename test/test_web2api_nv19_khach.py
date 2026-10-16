"""NV19 — lấp lỗ hổng NV14 nhóm khoá khách (CHỈ THÊM TEST, không sửa mã).

4. app/khoa_khach.py:ap_chinh_sach_chu bỏ chính sách chủ → chủ bị xoá/tắt
   thì khoá của chủ không còn dùng được (bat=False).
5. app/khoa_khach.py:giao_pham_vi bỏ giao với trần → phạm vi hiệu lực
   phải bị thu hẹp theo trần.
6. app/khoa_khach.py:ban_ghi_theo_bam bỏ kiểm định dạng băm → băm lạ
   (kể cả sai kiểu) bị từ chối, không 500.
"""

from __future__ import annotations

import asyncio

import pytest

from app import khoa_khach, nguoi_dung

KHOA_QT = "khoa-quan-tri-nv19-khach-0123456789abcdef"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    khoa_khach.lam_moi_cache()
    return tmp_path


def _tao_chu_va_khoa() -> tuple[str, str]:
    """Tạo người dùng (vai user) + khoá khách thuộc chủ đó; trả (chu_id, khoa_tho)."""
    ban_ghi_nd, _ten, _mk = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user"))
    chu_id = ban_ghi_nd["id"]
    _ban_ghi_khoa, khoa_tho = khoa_khach.tao_khoa(
        nhan="khoá của chủ test", pham_vi=["*"], chu_id=chu_id)
    return chu_id, khoa_tho


def test_khoa_cua_chu_bi_xoa_khong_dung_duoc(moi_truong):
    """NV14#4: bỏ chính sách chủ trong ap_chinh_sach_chu → chủ bị xoá thì khoá bị đóng."""
    chu_id, khoa_tho = _tao_chu_va_khoa()
    truoc = khoa_khach.tim_theo_khoa_tho(khoa_tho)
    assert truoc is not None and truoc.get("bat") is True, "khoá mới tạo phải dùng được"
    assert nguoi_dung.xoa_nguoi_dung(chu_id) is True
    sau = khoa_khach.tim_theo_khoa_tho(khoa_tho)
    assert sau is not None, "khoá biến mất hẳn thay vì bị đóng"
    assert sau.get("bat") is False, "chủ đã bị xoá mà khoá vẫn dùng được"


def test_khoa_cua_chu_bi_tat_khong_dung_duoc(moi_truong):
    """NV14#4: bỏ chính sách chủ trong ap_chinh_sach_chu → chủ bị tắt thì khoá bị đóng."""
    chu_id, khoa_tho = _tao_chu_va_khoa()
    nguoi_dung.sua_nguoi_dung(chu_id, bat=False)
    sau = khoa_khach.tim_theo_khoa_tho(khoa_tho)
    assert sau is not None, "khoá biến mất hẳn thay vì bị đóng"
    assert sau.get("bat") is False, "chủ bị tắt mà khoá vẫn dùng được"


def test_giao_pham_vi_thu_hep_theo_tran():
    """NV14#5: bỏ giao với trần trong giao_pham_vi → phạm vi phải bị thu hẹp theo trần."""
    assert khoa_khach.giao_pham_vi(["chat", "anh", "doc"], ["anh"]) == ["anh"]
    assert khoa_khach.giao_pham_vi(["chat"], ["anh"]) == []
    assert khoa_khach.giao_pham_vi(["*"], ["anh", "doc"]) == ["anh", "doc"]
    assert khoa_khach.giao_pham_vi(["anh"], ["*"]) == ["anh"]


@pytest.mark.parametrize("bam_la", [
    "khong-phai-bam",
    "z" * 63,          # sai độ dài
    "z" * 65,          # sai độ dài
    "g" * 64,          # đủ dài nhưng không phải hex
    "",
    None,              # sai kiểu
    123,               # sai kiểu
    ["a"],             # sai kiểu
    b"bytes",          # sai kiểu
])
def test_bam_la_bi_tu_choi(moi_truong, bam_la):
    """NV14#6: bỏ kiểm định dạng băm → băm lạ (kể cả sai kiểu) bị từ chối, không 500.

    Kho có sẵn một khoá thật: nếu bỏ kiểm định dạng, băm sai kiểu (None/123/...)
    sẽ nổ AttributeError trong vòng lặp so sánh thay vì được từ chối gọn.
    """
    khoa_khach.tao_khoa(nhan="khoá mồi", pham_vi=["chat"])
    assert khoa_khach.ban_ghi_theo_bam(bam_la) is None
