"""Test bộ máy khôi phục phiên nhiều tầng NV88 (app/khoi_phuc_phien.py).

Tầng (a) làm mới bằng cookie đang có → (b) đăng nhập lại tự động bằng mật khẩu
đã lưu (chốt dừng: 2 lần hỏng liên tiếp thì ngừng tự đăng nhập) → (c) chuyển
tài khoản/khoá kế → (d) can_nguoi + mở noVNC.

Module là giả (không mạng, không Chrome). Lõi không chứa tên nhà cung cấp nào:
mọi đặc thù nằm trong các phương thức tuỳ chọn của module (`lam_moi_phien`,
`dang_nhap_tu_dong`, `tai_khoan_ke`, `danh_dau_hong`, `mat_khau_da_luu`,
`co_tai_khoan`, `ho_so_trinh_duyet`).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import khoi_phuc_phien as kp
from app.tai_khoan_hang_loat import BiMatTam

MAT_KHAU_GIA = "mat-khau-gia-nv88-001"
COOKIE_GIA = "phien-gia-nv88-aaaabbbbcccc"


def chay(coro):
    return asyncio.run(coro)


class ModuleGia:
    """Module giả cho test lõi: kịch bản theo tầng, không mạng thật."""

    def __init__(self, manifest=None):
        self.manifest = manifest or {"id": "mod-gia-nv88", "tien_to": "modgianv88"}
        self.kich_ban = {}          # "a"/"b" -> KetQuaKhoiPhuc hoặc Exception
        self.khoa_ke = None         # tầng (c): khoá kế, None = hết
        self.mat_khau_luu = None    # str | None — mật khẩu đã lưu trong kho module
        self.da_goi = []            # [(tang, ...)] để kiểm thứ tự
        self.da_danh_dau = []       # [(khoa, ma_loi)]
        self.mat_khau_da_xoa = None  # BiMatTam module đưa ra (để kiểm engine xoá bản sao)

    async def lam_moi_phien(self, khoa):
        self.da_goi.append(("a", khoa))
        kq = self.kich_ban.get("a")
        if isinstance(kq, Exception):
            raise kq
        return kq if kq is not None else kp.KetQuaKhoiPhuc(False, ma_loi="het_han")

    async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
        self.da_goi.append(("b", tai_khoan.khoa))
        kq = self.kich_ban.get("b")
        if isinstance(kq, Exception):
            raise kq
        return kq if kq is not None else kp.KetQuaKhoiPhuc(False, ma_loi="sai_mat_khau")

    def mat_khau_da_luu(self, khoa):
        if self.mat_khau_luu is None:
            return None
        bm = BiMatTam(self.mat_khau_luu)
        self.mat_khau_da_xoa = bm
        return bm

    def tai_khoan_ke(self, khoa):
        self.da_goi.append(("c", khoa))
        return self.khoa_ke

    def danh_dau_hong(self, khoa, ma_loi):
        self.da_danh_dau.append((khoa, ma_loi))

    def co_tai_khoan(self, khoa):
        return khoa in ("tk-mot", "tk-hai")


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def _tk(khoa="tk-mot", mat_khau=None):
    return kp.TaiKhoanCanKhoiPhuc(
        khoa=khoa, email="ban@example.com",
        mat_khau=BiMatTam(mat_khau) if mat_khau else None)


# ───────────── tầng (a): làm mới bằng cookie đang có ─────────────

def test_tang_a_thanh_cong(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(True)
    kq = chay(kp.khoi_phuc(mod, _tk()))
    assert kq.thanh_cong and kq.tang == "a"
    assert mod.da_goi == [("a", "tk-mot")]  # chỉ gọi tầng a


def test_tang_a_hong_module_nem_loi_van_sang_b(du_lieu):
    """Module ném ngoại lệ ở tầng (a) → coi như hỏng tầng, vẫn sang (b)."""
    mod = ModuleGia()
    mod.kich_ban["a"] = RuntimeError("mang rot")
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(True)
    kq = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kq.thanh_cong and kq.tang == "b"
    assert [t for t, _ in mod.da_goi] == ["a", "b"]


# ───────────── tầng (b): đăng nhập lại tự động ─────────────

def test_tang_b_thanh_cong_bang_mat_khau_dua_truc_tiep(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han", ma_trang_thai=401)
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(True)
    bm = BiMatTam(MAT_KHAU_GIA)
    kq = chay(kp.khoi_phuc(mod, kp.TaiKhoanCanKhoiPhuc(khoa="tk-mot", mat_khau=bm)))
    assert kq.thanh_cong and kq.tang == "b"
    assert bm.da_xoa()  # bí mật đưa vào đã bị xoá sau dùng
    assert [t for t, _ in mod.da_goi] == ["a", "b"]


def test_tang_b_dung_mat_khau_luu_trong_kho_module(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(True)
    mod.mat_khau_luu = MAT_KHAU_GIA
    kq = chay(kp.khoi_phuc(mod, _tk()))  # không đưa mật khẩu trực tiếp
    assert kq.thanh_cong and kq.tang == "b"
    assert ("b", "tk-mot") in mod.da_goi
    # bản sao mật khẩu module đưa ra đã bị engine xoá khỏi bộ nhớ
    assert mod.mat_khau_da_xoa is not None and mod.mat_khau_da_xoa.da_xoa()


def test_tang_b_bo_qua_khi_chi_cookie(du_lieu):
    """«Chỉ dùng cookie, không lưu mật khẩu» → tầng (b) bị bỏ qua hoàn toàn."""
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.mat_khau_luu = MAT_KHAU_GIA
    mod.khoa_ke = "tk-hai"
    kq = chay(kp.khoi_phuc(mod, _tk(), dung_mat_khau_luu=False))
    assert kq.thanh_cong and kq.tang == "c" and kq.khoa_moi == "tk-hai"
    assert "b" not in [t for t, _ in mod.da_goi]


def test_tang_b_hong_hai_lan_lien_tiep_thi_ngung_tu_dang_nhap(du_lieu):
    """Chốt dừng: 2 lần hỏng liên tiếp ở tầng (b) → ngừng tự đăng nhập tài khoản đó."""
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(False, ma_loi="sai_mat_khau", ma_trang_thai=401)
    mod.khoa_ke = "tk-hai"
    kq1 = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kq1.thanh_cong and kq1.tang == "c"  # lần 1 vẫn sang tầng c
    assert kp.lay_trang_thai("mod-gia-nv88", "tk-mot")["hong_b_lien_tiep"] == 1

    kq2 = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kq2.thanh_cong and kq2.tang == "c"
    tt = kp.lay_trang_thai("mod-gia-nv88", "tk-mot")
    assert tt["hong_b_lien_tiep"] == 2
    assert tt["ngung_tu_dang_nhap"] is True

    # lần 3: tầng (b) KHÔNG được gọi nữa dù vẫn đưa mật khẩu
    mod.da_goi.clear()
    kq3 = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kq3.thanh_cong and kq3.tang == "c"
    assert "b" not in [t for t, _ in mod.da_goi]


def test_tang_b_thanh_cong_dat_lai_bo_dem(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(False, ma_loi="sai_mat_khau")
    chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kp.lay_trang_thai("mod-gia-nv88", "tk-mot")["hong_b_lien_tiep"] == 1
    mod.kich_ban["b"] = kp.KetQuaKhoiPhuc(True)
    kq = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kq.thanh_cong and kq.tang == "b"
    assert kp.lay_trang_thai("mod-gia-nv88", "tk-mot")["hong_b_lien_tiep"] == 0


# ───────────── tầng (c): chuyển tài khoản kế ─────────────

def test_tang_c_chuyen_khoa_ke_va_danh_dau_hong(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han", ma_trang_thai=401)
    mod.khoa_ke = "tk-hai"
    kq = chay(kp.khoi_phuc(mod, _tk(), dung_mat_khau_luu=False))
    assert kq.thanh_cong and kq.tang == "c" and kq.khoa_moi == "tk-hai"
    assert mod.da_danh_dau == [("tk-mot", "het_han")]


def test_tang_c_het_khoa_ke_thi_sang_d(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.khoa_ke = None
    da_mo = []
    kq = chay(kp.khoi_phuc(mod, _tk(), dung_mat_khau_luu=False,
                           mo_novnc=lambda m, hs, nd: da_mo.append((m, hs)) or "/novnc/dang-nhap?ve=VE_GIA"))
    assert not kq.thanh_cong and kq.can_nguoi and kq.tang == "d"
    assert da_mo == [("mod-gia-nv88", "mod-gia-nv88-tk-mot")]
    assert "mở noVNC" in kq.huong_dan or "noVNC" in kq.huong_dan


# ───────────── tầng (d) / can_nguoi ─────────────

def test_can_nguoi_tu_tang_a_dung_ngay_khong_sang_b_c(du_lieu):
    """CAPTCHA/2FA ở tầng (a) → dừng ngay ở can_nguoi, không thử (b)/(c)."""
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, can_nguoi=True,
                                          huong_dan="Gặp màn hình xác minh.")
    mod.khoa_ke = "tk-hai"
    kq = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    assert kq.can_nguoi and kq.tang == "d"
    assert [t for t, _ in mod.da_goi] == ["a"]
    tt = kp.lay_trang_thai("mod-gia-nv88", "tk-mot")
    assert tt["can_nguoi"] is True


def test_trang_thai_can_nguoi_luu_0600_va_xoa_duoc(du_lieu, tmp_path):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.khoa_ke = None
    chay(kp.khoi_phuc(mod, _tk(), dung_mat_khau_luu=False))
    tep = tmp_path / "data" / "bi_mat" / "khoi_phuc_phien.json"
    assert tep.exists()
    import stat
    assert stat.S_IMODE(tep.stat().st_mode) == 0o600
    assert kp.lay_trang_thai("mod-gia-nv88", "tk-mot")["can_nguoi"] is True
    kp.xoa_can_nguoi("mod-gia-nv88", "tk-mot")
    assert kp.lay_trang_thai("mod-gia-nv88", "tk-mot")["can_nguoi"] is False


# ───────────── vệ sinh bí mật và mã lỗi ─────────────

def test_ma_loi_la_bi_thay_bang_phan_loai_theo_ma_trang_thai(du_lieu):
    """Module đưa ma_loi dài/lạ → lõi thay bằng phân loại theo mã trạng thái (không dò chữ)."""
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="Lỗi rất dài có dấu và ký tự lạ !!!",
                                          ma_trang_thai=429)
    mod.khoa_ke = "tk-hai"
    kq = chay(kp.khoi_phuc(mod, _tk(), dung_mat_khau_luu=False))
    assert kq.thanh_cong and kq.tang == "c"
    assert mod.da_danh_dau == [("tk-mot", "han_muc")]


def test_ket_qua_sang_dict_khong_lo_bi_mat(du_lieu):
    mod = ModuleGia()
    mod.kich_ban["a"] = kp.KetQuaKhoiPhuc(False, ma_loi="het_han")
    mod.khoa_ke = None
    kq = chay(kp.khoi_phuc(mod, _tk(mat_khau=MAT_KHAU_GIA)))
    ban_json = json.dumps(kq.sang_dict(), ensure_ascii=False)
    assert MAT_KHAU_GIA not in ban_json
    assert COOKIE_GIA not in ban_json


def test_khong_co_phuong_thuc_nao_thi_thang_vao_d(du_lieu):
    """Module không triển khai khả năng nào → thẳng tầng (d) can_nguoi."""

    class ModTrong:
        manifest = {"id": "mod-trong", "tien_to": "modtrong"}

    kq = chay(kp.khoi_phuc(ModTrong(), _tk()))
    assert kq.can_nguoi and kq.tang == "d"


# ───────────── đường tới hồ sơ trình duyệt (chống SSRF/đường dẫn) ─────────────

def test_duong_ho_so_hop_le_nam_duoi_goc(du_lieu, tmp_path):
    p = kp.duong_ho_so_trinh_duyet("chatgpt-web-tk-a1b2c3")
    goc = (tmp_path / "data" / "ho-so-trinh-duyet").resolve()
    assert p.resolve().is_relative_to(goc)


def test_duong_ho_so_chan_di_nguoc_thu_muc(du_lieu):
    import pytest as _pt
    for xau in ("../thoat", "..", "a/b", "", "co dau cach", ";rm", "a" * 65):
        with _pt.raises(ValueError):
            kp.duong_ho_so_trinh_duyet(xau)


# ───────────── trình duyệt giả ─────────────

def test_trinh_duyet_gia_ghi_hanh_dong_va_doc_cookie():
    td = kp.TrinhDuyetGia(
        trang={"https://dang-nhap.gia/": {"tin_hieu": ["thanh_cong"],
                                          "cookie": {"phien": COOKIE_GIA}}})
    chay(td.mo_trang("https://dang-nhap.gia/"))
    chay(td.dien("email", "ban@example.com"))
    chay(td.bam("tiep-tuc"))
    assert td.doc_cookie("dang-nhap.gia") == {"phien": COOKIE_GIA}
    assert [h[0] for h in td.hanh_dong] == ["mo_trang", "dien", "bam"]
    # giá trị điền vào không nằm trong nhật ký hành động ở dạng thô
    assert "ban@example.com" not in json.dumps([h[0] for h in td.hanh_dong])
