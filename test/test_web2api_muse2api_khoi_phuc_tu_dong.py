"""Test khoi_phuc_tu_dong RIÊNG của module muse2api (không đăng nhập tươi).

muse.ai đăng nhập qua SSO Meta, KHÔNG có form mật khẩu để tự điền — nên
khoi_phuc_tu_dong KHÔNG được bịa luồng mật khẩu/TOTP, mà chỉ:
  T0 kiểm cookie bằng bootstrap (NV230 trong đó đã thử lấy cookie mới từ
     workspace Firefox đã gắn, kèm kiểm phiên sau khi thay);
  T1 nghỉ-kiểm lại;
  T2 tái dùng hồ sơ đã đăng nhập qua controller (tai_dung_ho_so, NV93),
     không điền mật khẩu/2FA; không có controller/hồ sơ → can_nguoi rõ ràng;
  T3 kiểm lại khoá mới bằng bootstrap, qua mới tính thành công.
Làm mới token: protocol không có refresh_token — _lam_moi_token_tu_cookie
chỉ kiểm bootstrap từ cookie còn sống, không đổi trạng thái tài khoản.

Không Internet, không trình duyệt thật, không cookie thật — mọi chuỗi
đều GIẢ rõ ràng, _kiem_cookie được chích giả như test NV90/NV230.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app import kho
from app.tai_khoan_hang_loat import BiMatTam
from modules.muse2api import module as mm

GOC = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (GOC / "modules" / "muse2api" / "manifest.json").read_text(encoding="utf-8"))

HO_SO = "a1b2c3d4e5f6a7b8c9d0"
COOKIE_OK = "hatch_sess=ok-gia-khoi-phuc; hatch_gw=gw-gia"
COOKIE_HET_HAN = "hatch_sess=het-han-gia-khoi-phuc; hatch_gw=gw-gia"
COOKIE_MOI = "hatch_sess=sess-moi-gia-khoi-phuc; hatch_gw=gw-moi-gia"
SESS_MOI = "sess-moi-gia-khoi-phuc"
MAT_KHAU_GIA = "mat-khau-gia-khoi-phuc-001"


def chay(coro):
    return asyncio.run(coro)


def _kiem_cookie_gia(cookies: str):
    if "het-han-gia" in cookies:
        return False, "cookie het han (gia)", True
    if "loi-mang-gia" in cookies:
        return False, "timeout (gia)", False
    return True, None, False


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(mm.Module, "_kiem_cookie",
                        staticmethod(_kiem_cookie_gia))
    md = mm.Module(dict(MANIFEST))
    md.kho = kho.kho_rieng("muse2api")
    return md


def _gan_ho_so(md, cookie: str, ho_so: str = HO_SO) -> None:
    hs = mm._hatch_sess_tu_cookie(cookie)
    khoa93 = md._khoa_tu_hatch_sess(hs)
    bg = {"khoa": khoa93, "email": "ban-gia-khoi-phuc@example.com",
          "ho_so": ho_so, "goi": "khong_ro", "trang_thai": "hoat_dong",
          "lan_dung_cuoi": None}
    md.kho.luu("tai_khoan_nv93", {"ban_ghi": {khoa93: bg}})


class TrinhDuyetGia:
    """Controller GIẢ: chỉ mở trang, đọc tín hiệu, đọc cookie — KHÔNG dien()."""

    def __init__(self, tin_hieu=None, hatch_sess=None):
        self.tin_hieu_tra = set(tin_hieu) if tin_hieu is not None else {"da_dang_nhap"}
        self.hatch_sess = hatch_sess
        self.dem_mo_trang = 0
        self.dem_dien = 0

    async def mo_trang(self, url):
        self.dem_mo_trang += 1

    async def tin_hieu(self, quy_tac=None):
        return set(self.tin_hieu_tra)

    async def bam(self, bo_chon):
        pass

    async def doc_cookie(self, mien):
        return {"hatch_sess": self.hatch_sess} if self.hatch_sess else {}

    async def dien(self, truong, gia_tri):
        self.dem_dien += 1
        raise AssertionError("muse2api không được dien() mật khẩu/2FA")


class TotpGia:
    def __init__(self):
        self.da_xoa = False

    def xoa(self):
        self.da_xoa = True


def test_t0_cookie_con_song(mod):
    mod.nap({"cookies": [COOKIE_OK]})
    khoa = mod._khoa_tu_cookies(COOKIE_OK)
    kq = chay(mod.khoi_phuc_tu_dong(khoa, ""))
    assert kq.thanh_cong and kq.tang == "T0", kq


def test_t1_nghi_roi_kiem_lai_song(mod, monkeypatch):
    # Lượt kiểm đầu hỏng, các lượt sau sống → T1 thành công.
    trang_thai = {"dem": 0}

    def _kiem_lan_dau_hong(cookies: str):
        trang_thai["dem"] += 1
        if trang_thai["dem"] == 1:
            return False, "cookie het han (gia)", True
        return True, None, False

    monkeypatch.setattr(mm.Module, "_kiem_cookie",
                        staticmethod(_kiem_lan_dau_hong))
    mod.nap({"cookies": [COOKIE_OK]})
    khoa = mod._khoa_tu_cookies(COOKIE_OK)
    kq = chay(mod.khoi_phuc_tu_dong(khoa, "", nghi_t1_giay=0.01))
    assert kq.thanh_cong and kq.tang == "T1", kq


def test_t2_khong_trinh_duyet_thi_can_nguoi_va_xoa_bi_mat(mod):
    mod.nap({"cookies": [COOKIE_HET_HAN]})
    khoa = mod._khoa_tu_cookies(COOKIE_HET_HAN)
    mk = BiMatTam(MAT_KHAU_GIA)
    totp = TotpGia()
    kq = chay(mod.khoi_phuc_tu_dong(khoa, "", mat_khau=mk, totp=totp))
    assert not kq.thanh_cong and kq.tang == "T2", kq
    assert kq.ma_loi == "can_nguoi" and kq.can_nguoi
    assert "Meta" in kq.huong_dan
    assert mk.da_xoa()
    assert totp.da_xoa


def test_t2_tai_dung_ho_so_roi_t3_kiem_lai(mod):
    _gan_ho_so(mod, COOKIE_HET_HAN)
    mod.nap({"cookies": [COOKIE_HET_HAN]})
    khoa = mod._khoa_tu_cookies(COOKIE_HET_HAN)
    td = TrinhDuyetGia(hatch_sess=SESS_MOI)
    kq = chay(mod.khoi_phuc_tu_dong(khoa, "", trinh_duyet=td))
    assert kq.thanh_cong and kq.tang == "T3", kq
    assert td.dem_dien == 0  # không điền mật khẩu/2FA
    # tai_dung_ho_so chỉ thêm "hatch_sess=<mới>" (không kèm hatch_gw cũ),
    # nên khoá mới tính trên chuỗi đó, không phải COOKIE_MOI đầy đủ.
    khoa_moi = mod._khoa_tu_cookies("hatch_sess=" + SESS_MOI)
    assert kq.khoa_moi == khoa_moi
    assert mod.co_tai_khoan(khoa_moi)


def test_t2_can_nguoi_roi_hoan_captcha_1800_giay(mod):
    _gan_ho_so(mod, COOKIE_HET_HAN)
    mod.nap({"cookies": [COOKIE_HET_HAN]})
    khoa = mod._khoa_tu_cookies(COOKIE_HET_HAN)
    td = TrinhDuyetGia(tin_hieu={"captcha"}, hatch_sess=None)
    kq = chay(mod.khoi_phuc_tu_dong(khoa, "", trinh_duyet=td))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi", kq
    dem = td.dem_mo_trang
    # Gọi lại ngay: T0 vẫn hỏng, T2 bị hoãn → captcha_hoan, không gọi
    # trình duyệt lại.
    kq2 = chay(mod.khoi_phuc_tu_dong(khoa, "", trinh_duyet=td))
    assert not kq2.thanh_cong and kq2.ma_loi == "captcha_hoan", kq2
    assert td.dem_mo_trang == dem
    # Xoá trạng thái → T2 lại chạy trình duyệt.
    mod.dat_lai_trang_thai_tu_dong()
    kq3 = chay(mod.khoi_phuc_tu_dong(khoa, "", trinh_duyet=td))
    assert kq3.ma_loi == "can_nguoi", kq3
    assert td.dem_mo_trang > dem


def test_khoa_khong_co(mod):
    kq = chay(mod.khoi_phuc_tu_dong("ms-khong-co", ""))
    assert not kq.thanh_cong and kq.ma_loi == "khoa_khong_co", kq


def test_anh_xa_email_nv93_cho_tk_id_ngoai_module(mod):
    # Delegate MCP đưa tk_id ngoài module + email: module ánh xạ qua
    # bản ghi NV93 (email + hatch_sess) → khoá ms- thật, T0 thành công.
    # Tài khoản dán cookie cũ không có email trong module thì không ánh
    # xạ được (xem test_khoa_khong_co ở trên).
    _gan_ho_so(mod, COOKIE_OK)
    mod.nap({"cookies": [COOKIE_OK]})
    kq = chay(mod.khoi_phuc_tu_dong("tk-ngoai-module-gia",
                                     "ban-gia-khoi-phuc@example.com"))
    assert kq.thanh_cong and kq.tang == "T0", kq


def test_lam_moi_token_tu_cookie_khong_doi_trang_thai(mod):
    mod.nap({"cookies": [COOKIE_OK]})
    acc = mod._accs[0]
    assert mod._lam_moi_token_tu_cookie(COOKIE_OK)["ok"] is True
    assert mod._lam_moi_token_tu_cookie(COOKIE_HET_HAN) == {
        "ok": False, "ma_loi": "het_han"}
    # Không đổi cookie, không tạo Gateway, không đổi khoe.
    assert acc.cookies == COOKIE_OK and acc._gw is None


class DichVuGiaNv230:
    """Dịch vụ trình duyệt GIẢ, cùng hình dạng client vnc-xa (như NV230)."""

    def __init__(self):
        self.ho_so_ds = [{"ho_so": HO_SO, "ten": "muse gia",
                          "loai": "firefox", "dang_mo": False}]
        self.cookie_theo_ho_so = {HO_SO: COOKIE_MOI}

    async def trinh_duyet_ds(self):
        return [dict(x) for x in self.ho_so_ds]

    async def trinh_duyet_cookie(self, ho_so, mien):
        assert mien == "muse.ai"
        c = self.cookie_theo_ho_so.get(ho_so)
        if c is None:
            raise _LoiDichVuGia(404)
        return {"cookie": c, "so_cookie": 3}


class _LoiDichVuGia(Exception):
    def __init__(self, ma: int):
        super().__init__(f"dich vu gia tra {ma}")
        self.ma = ma


def test_lam_moi_phien_kiem_lai_sau_khi_thay_cookie(mod, monkeypatch):
    # Giả NV230: dịch vụ trả cookie mới; lần kiểm đầu của cookie mới qua
    # (kiểm trước khi thay), lần kiểm sau hỏng → lam_moi_phien KHÔNG được
    # báo thành công.
    dem_moi = {"n": 0}

    def _kiem_co_dem(cookies: str):
        if cookies == COOKIE_MOI:
            dem_moi["n"] += 1
            if dem_moi["n"] == 1:
                return True, None, False
            return False, "cookie moi hong sau khi thay (gia)", True
        return _kiem_cookie_gia(cookies)

    monkeypatch.setattr(mm.Module, "_kiem_cookie",
                        staticmethod(_kiem_co_dem))
    _gan_ho_so(mod, COOKIE_HET_HAN)
    mod.nap({"cookies": [COOKIE_HET_HAN]})
    khoa = mod._khoa_tu_cookies(COOKIE_HET_HAN)
    dv = DichVuGiaNv230()
    mod._bo_lam_moi_cookie._tao_client_dich_vu = lambda: dv
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_loi == "het_han", kq
    assert mod._accs[0].khoe is False
