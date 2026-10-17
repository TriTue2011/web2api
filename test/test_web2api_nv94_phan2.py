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


# ── (e) nối NV91↔NV92: dang_chay tới khoi_phuc ─────────────────────────────

class TestTuLamMoiNoiDangChay:
    def test_bat_dau_nho_lay_dang_chay(self):
        import asyncio
        from app import tu_lam_moi

        async def chay():
            ql = tu_lam_moi.QuanLyTuLamMoi()
            ham = lambda: {"m1": object()}
            ql.bat_dau(ham)
            try:
                assert ql._lay_dang_chay is ham
            finally:
                await ql.dung()

        asyncio.run(chay())

    def test_chay_khoi_phuc_truyen_dang_chay(self, monkeypatch):
        import asyncio
        from app import khoi_phuc_phien as kp
        from app import tu_lam_moi

        da_goi = {}

        async def gia_khoi_phuc(module, tai_khoan, **kw):
            da_goi.update(kw)
            return kp.KetQuaKhoiPhuc(True, tang="a")

        monkeypatch.setattr(kp, "khoi_phuc", gia_khoi_phuc)
        dc = {"m1": object()}
        tu_lam_moi.dang_ky_dang_chay(lambda: dc)

        class ModGia:
            async def lam_moi_phien(self, khoa):
                return kp.KetQuaKhoiPhuc(False, tang="a", ma_loi="hong")

        async def chay():
            await tu_lam_moi._chay_khoi_phuc(ModGia(), "m1", "k1", "")

        asyncio.run(chay())
        assert da_goi.get("dang_chay") is dc, \
            "_chay_khoi_phuc chưa truyền dang_chay cho khoi_phuc"


# ── (b) quy tắc tín hiệu bằng cấu trúc ────────────────────────────────────

QUY_TAC_MAU = [
    {"loai": "cookie", "mien": "google.com", "ten": "SID",
     "tin_hieu": "da_dang_nhap"},
    {"loai": "url_chua", "gia_tri": "accounts.google.com/challenge",
     "tin_hieu": "thu_thach_truy_cap"},
]


class TestTinHieuTheoQuyTac:
    def test_trinh_duyet_gia_danh_gia_quy_tac_cookie(self):
        from app.khoi_phuc_phien import TrinhDuyetGia
        td = TrinhDuyetGia({"https://myaccount.google.com/":
                            {"cookie": {"SID": "abc"}}})

        async def chay():
            await td.mo_trang("https://myaccount.google.com/")
            return td.tin_hieu(QUY_TAC_MAU)

        import asyncio
        assert "da_dang_nhap" in asyncio.run(chay())

    def test_trinh_duyet_gia_danh_gia_quy_tac_url(self):
        from app.khoi_phuc_phien import TrinhDuyetGia
        td = TrinhDuyetGia({})

        async def chay():
            await td.mo_trang("https://accounts.google.com/challenge/abc")
            return td.tin_hieu(QUY_TAC_MAU)

        import asyncio
        assert "thu_thach_truy_cap" in asyncio.run(chay())

    def test_ho_so_google_khai_quy_tac_cau_truc(self):
        import sys
        sys.path.insert(0, "modules/ho-so-google")
        try:
            import importlib
            mod = importlib.import_module("module")
            m = mod.Module({})
            qt = m.quy_tac_tin_hieu()
            assert isinstance(qt, list) and qt
            for q in qt:
                assert q["loai"] in ("cookie", "url_chua", "tieu_de_chua", "bo_chon")
                assert "tin_hieu" in q
            # Không dò chữ: không quy tắc nào mang nội dung văn bản trang
            assert not any("van_ban" in q or "text" in q for q in qt)
            ten_th = {q["tin_hieu"] for q in qt}
            assert "da_dang_nhap" in ten_th
        finally:
            sys.path.remove("modules/ho-so-google")

    def test_ho_so_google_di_tat_bang_quy_tac(self):
        import asyncio, sys
        sys.path.insert(0, "modules/ho-so-google")
        try:
            import importlib
            mod = importlib.import_module("module")
            from app.khoi_phuc_phien import TrinhDuyetGia
            m = mod.Module({})
            td = TrinhDuyetGia({"https://myaccount.google.com/":
                                {"cookie": {"SID": "abc"}}})

            async def chay():
                return await m.dang_nhap("abcd1234abcd1234", "a@b.c", None, td)

            kq = asyncio.run(chay())
            assert kq.thanh_cong and kq.buoc == "di_tat", \
                "quy tắc cấu trúc chưa được truyền qua tin_hieu"
        finally:
            sys.path.remove("modules/ho-so-google")
