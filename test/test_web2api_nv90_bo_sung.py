"""Test patch bổ sung NV90: helper vượt thử thách dùng chung ở lõi.

- `app.khoi_phuc_phien.vuot_thu_thach_toi_da`: logic «nhận ra thử thách bằng
  cấu trúc + cố vượt tối đa 3 lượt + bó tay thì False» nằm ở lõi (mã mở, không
  tên nhà cung cấp) để NV91 tầng (b) và mọi module web dùng chung; NV92 thay
  TrinhDuyetGia bằng TrinhDuyetThat thì helper vẫn chạy qua hợp đồng
  BoDieuKhienTrinhDuyet.
- `app.khoi_phuc_phien.la_captcha_chu`: nhận diện captcha chữ bằng cấu trúc.
- Tầng (b) của `khoi_phuc`: gặp thử thách thì gọi helper lõi thay vì bỏ cuộc
  ngay; không qua được → can_nguoi mà không gọi module.
- grok-web: captcha chữ → can_nguoi NGAY, KHÔNG cố vượt/giải (không OCR vì
  cấm thêm gói; cấm dịch vụ giải hộ) — «phát hiện được, không giải được».

Trình duyệt là GIẢ (TrinhDuyetGia). Không Internet, không Chrome.
Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app import khoi_phuc_phien as kp
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_bo_sung_mod_test"

MAT_KHAU_GIA = "mat-khau-gia-bo-sung-001"
EMAIL_GIA = "ban-gia-bo-sung@example.com"


def chay(coro):
    return asyncio.run(coro)


def _tai_goi_grok():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


# ───────────── la_captcha_chu ─────────────

def test_la_captcha_chu_nhan_dien_bang_cau_truc():
    assert kp.la_captcha_chu({"captcha"}) is True
    assert kp.la_captcha_chu({"captcha", "thu_thach_truy_cap"}) is True
    assert kp.la_captcha_chu({"thanh_cong"}) is False
    assert kp.la_captcha_chu(set()) is False
    assert kp.la_captcha_chu(None) is False


# ───────────── vuot_thu_thach_toi_da ─────────────

def test_helper_khong_thu_thach_thi_true_ngay_khong_goi_vuot():
    td = TrinhDuyetGia(trang={"https://x.com/": {"tin_hieu": ["thanh_cong"]}})
    chay(td.mo_trang("https://x.com/"))
    assert chay(kp.vuot_thu_thach_toi_da(td)) is True
    assert td.hanh_dong == [("mo_trang", "https://x.com/")]


def test_helper_vuot_thanh_cong_thi_true():
    td = TrinhDuyetGia(trang={"https://x.com/": {
        "tin_hieu": ["thu_thach_truy_cap"],
        "vuot_thu_thach_thanh_cong": True}})
    chay(td.mo_trang("https://x.com/"))
    assert chay(kp.vuot_thu_thach_toi_da(td)) is True
    assert ("vuot_thu_thach",) in td.hanh_dong


def test_helper_vuot_that_bai_3_luot_thi_false():
    td = TrinhDuyetGia(trang={"https://x.com/": {
        "tin_hieu": ["thu_thach_truy_cap"],
        "vuot_thu_thach_thanh_cong": False}})
    chay(td.mo_trang("https://x.com/"))
    assert chay(kp.vuot_thu_thach_toi_da(td)) is False
    assert sum(1 for hd in td.hanh_dong if hd[0] == "vuot_thu_thach") == 3


def test_helper_loi_doc_tin_hieu_thi_false_dong_cua():
    class TdHong(TrinhDuyetGia):
        def tin_hieu(self):
            raise RuntimeError("hong gia")
    td = TdHong(trang={})
    assert chay(kp.vuot_thu_thach_toi_da(td)) is False


def test_helper_loi_khi_vuot_thi_false_dong_cua():
    class TdHongVuot(TrinhDuyetGia):
        async def vuot_thu_thach(self):
            raise RuntimeError("hong gia")
    td = TdHongVuot(trang={"https://x.com/": {"tin_hieu": ["thu_thach_truy_cap"]}})
    chay(td.mo_trang("https://x.com/"))
    assert chay(kp.vuot_thu_thach_toi_da(td)) is False


# ───────────── tầng (b) của khoi_phuc dùng helper lõi ─────────────

class _ModuleGiaB:
    def __init__(self):
        self.manifest = {"id": "mod-gia-bo-sung", "tien_to": "modgiabs"}
        self.da_goi_b = 0

    async def lam_moi_phien(self, khoa):
        return kp.KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han")

    async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
        self.da_goi_b += 1
        return kp.KetQuaKhoiPhuc(True, tang="b")

    def mat_khau_da_luu(self, khoa):
        return None

    def tai_khoan_ke(self, khoa):
        return None

    def danh_dau_hong(self, khoa, ma_loi):
        pass

    def ho_so_trinh_duyet(self, khoa):
        return "mod-gia-bo-sung"


def _tk_b():
    return kp.TaiKhoanCanKhoiPhuc(khoa="tk-mot", email=EMAIL_GIA,
                                 mat_khau=BiMatTam(MAT_KHAU_GIA))


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def test_tang_b_vuot_thanh_cong_thi_goi_module(du_lieu):
    """Trình duyệt báo thử thách, vượt được → tầng (b) gọi module như thường."""
    mod = _ModuleGiaB()
    td = TrinhDuyetGia(trang={"https://x.com/": {
        "tin_hieu": ["thu_thach_truy_cap"],
        "vuot_thu_thach_thanh_cong": True}})
    chay(td.mo_trang("https://x.com/"))
    kq = chay(kp.khoi_phuc(mod, _tk_b(), trinh_duyet=td, mo_novnc=lambda *a: None))
    assert kq.thanh_cong and kq.tang == "b"
    assert mod.da_goi_b == 1


def test_tang_b_vuot_that_bai_thi_can_nguoi_khong_goi_module(du_lieu):
    """Vượt không được sau 3 lượt → can_nguoi ngay, KHÔNG gọi module."""
    mod = _ModuleGiaB()
    td = TrinhDuyetGia(trang={"https://x.com/": {
        "tin_hieu": ["thu_thach_truy_cap"],
        "vuot_thu_thach_thanh_cong": False}})
    chay(td.mo_trang("https://x.com/"))
    kq = chay(kp.khoi_phuc(mod, _tk_b(), trinh_duyet=td, mo_novnc=lambda *a: None))
    assert not kq.thanh_cong and kq.can_nguoi and kq.ma_loi == "can_nguoi"
    assert mod.da_goi_b == 0
    assert sum(1 for hd in td.hanh_dong if hd[0] == "vuot_thu_thach") == 3


def test_tang_b_khong_trinh_duyet_thi_nhu_cu(du_lieu):
    """Không đưa trình duyệt → tầng (b) gọi module trực tiếp như trước."""
    mod = _ModuleGiaB()
    kq = chay(kp.khoi_phuc(mod, _tk_b(), mo_novnc=lambda *a: None))
    assert kq.thanh_cong and kq.tang == "b"
    assert mod.da_goi_b == 1


# ───────────── grok-web: captcha chữ → can_nguoi ngay, không cố vượt ─────────────

def test_grok_web_captcha_chu_thi_can_nguoi_ngay_khong_vuot(tmp_path, monkeypatch):
    """Tín hiệu captcha (kể cả kèm thu_thach_truy_cap) → can_nguoi NGAY;
    KHÔNG gọi vuot_thu_thach, KHÔNG điền form — «phát hiện được, không giải
    được» (cấm thêm gói nên không OCR; cấm dịch vụ giải hộ)."""
    from app import kho as kho_mod
    from app.tai_khoan_hang_loat import DongDaTach
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod_module = _tai_goi_grok()
    import json
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    mod = mod_module.Module(dict(manifest))
    mod.kho = kho_mod.kho_rieng("grok-web")
    td = TrinhDuyetGia(trang={"https://x.com/login": {
        "tin_hieu": ["captcha", "thu_thach_truy_cap"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert not any(hd[0] == "vuot_thu_thach" for hd in td.hanh_dong)
    assert not any(hd[0] == "dien" for hd in td.hanh_dong)
    assert tk.mat_khau.da_xoa()
