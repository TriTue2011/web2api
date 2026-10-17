"""NV94 Phần 2 — test cho các mục (a)..(e) và Phần 3.

Test-first: mỗi mục đỏ trước khi sửa mã. Dịch vụ trình duyệt GIẢ có hành vi
giống thật (hồ sơ chưa mở, id hex, tín hiệu theo quy tắc cấu trúc).
"""
from __future__ import annotations

import pytest


# ── (d) khuôn mã hồ sơ thống nhất ──────────────────────────────────────────

class TestMauHoSoThongNhat:
    def test_loi_dung_mau_chung_cua_danh_tinh(self):
        from app import danh_tinh, khoi_phuc_phien
        assert not hasattr(khoi_phuc_phien, "_MAU_ID_HO_SO"), \
            "khoi_phuc_phien còn regex riêng"
        assert khoi_phuc_phien.MAU_HO_SO_TRINH_DUYET is danh_tinh.MAU_HO_SO_TRINH_DUYET

    def test_tuyen_dung_mau_chung_cua_danh_tinh(self):
        from app import danh_tinh
        import app.tuyen as tuyen
        assert not hasattr(tuyen, "_MAU_HO_SO_TRINH_DUYET"), \
            "tuyen còn regex riêng"
        assert tuyen.MAU_HO_SO_TRINH_DUYET is danh_tinh.MAU_HO_SO_TRINH_DUYET

    def test_hex_khong_phai_hex_bi_loai(self):
        from app import danh_tinh
        mau = danh_tinh.MAU_HO_SO_TRINH_DUYET
        assert mau.fullmatch("a" * 16)
        assert not mau.fullmatch("chatgpt-web-" + "a" * 16)
        assert not mau.fullmatch("ABCDEF1234567890")


class TestHoSoThatCuaTaiKhoan:
    """Cầu nối trình duyệt thật: tài khoản → danh_tinh_id → ho_so hex.

    Module kiểu cũ (chỉ có ho_so_trinh_duyet "<module>-<hex>") → None, không đoán.
    """

    @pytest.fixture()
    def kho_tam(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
        from app import danh_tinh
        dt = danh_tinh.tao("nv94@example.com", "nv94")
        danh_tinh.cap_nhat_trang_thai(dt["id"], ho_so="ab12cd34ef56ab78")
        return dt["id"]

    def test_tra_hex_tu_danh_tinh(self, kho_tam):
        from app import khoi_phuc_phien

        class ModGia:
            def danh_tinh_id_cua(self, khoa):
                return kho_tam

        assert khoi_phuc_phien._ho_so_that_cua(ModGia(), "bat-ky") == "ab12cd34ef56ab78"

    def test_khong_doan_tu_ho_so_trinh_duyet_cu(self, kho_tam):
        from app import khoi_phuc_phien

        class ModCu:
            def ho_so_trinh_duyet(self, khoa):
                return "chatgpt-web-ab12cd34ef56ab78"

        assert khoi_phuc_phien._ho_so_that_cua(ModCu(), "bat-ky") is None

    def test_danh_tinh_khong_co_ho_so(self, kho_tam):
        from app import danh_tinh, khoi_phuc_phien

        class ModGia:
            def danh_tinh_id_cua(self, khoa):
                return "khong-ton-tai"

        assert khoi_phuc_phien._ho_so_that_cua(ModGia(), "k") is None

    def test_lay_trinh_duyet_that_tu_choi_khong_hex(self):
        from app import khoi_phuc_phien
        assert khoi_phuc_phien.lay_trinh_duyet_that("chatgpt-web-ab12cd34ef56", {}) is None
