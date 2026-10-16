"""NV19 — lấp lỗ hổng NV14#8: _lam_sach bỏ che_chuoi (CHỈ THÊM TEST, không sửa mã).

Ghi một chuỗi dạng token (GIẢ — chuỗi này đã liệt kê trong allowlist của
.gitleaks.toml nên không phải bí mật thật) qua nhat_ky.ghi rồi đọc tệp trên
đĩa: chuỗi thô không được xuất hiện. Chuỗi đi qua _lam_sach (kwargs), đúng
chỗ đột biến NV14#8 tác động.
"""

from __future__ import annotations

import pytest

from app import nhat_ky

# Chuỗi GIẢ dạng khoá Google — dựng lúc chạy từ các mảnh để trong tệp KHÔNG có
# literal nào dạng token (tránh gitleaks báo nhầm). Dạng này (tiền tố AIza, chuỗi
# toàn số 0, hậu tố tot) đã liệt kê trong allowlist của .gitleaks.toml.
TOKEN_GIA = "AIza" + "SyUI-TOT-" + "0" * 28 + "tot1"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-nv19-nhatky-0123456789abcdef")
    return tmp_path


def test_lam_sach_che_token_truoc_khi_ghi_dia(moi_truong):
    """NV14#8: bỏ che_chuoi trong _lam_sach → chuỗi thô lọt ra tệp nhật ký trên đĩa."""
    nhat_ky.ghi("kenh_test", "thong_tin", "tom tat binh thuong", khoa=TOKEN_GIA)
    p = nhat_ky._p_hien_tai()
    assert p.is_file(), "nhật ký chưa được ghi ra đĩa"
    noi_dung = p.read_bytes()
    assert TOKEN_GIA.encode("utf-8") not in noi_dung, "chuỗi dạng token lọt ra tệp nhật ký"
    # Đối chứng: dòng vẫn được ghi (bị che), không mất log
    assert b"khoa" in noi_dung
