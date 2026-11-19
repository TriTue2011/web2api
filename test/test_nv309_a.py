"""NV309-a — nối cầu nối trình duyệt thật vào MCP + đường module.dang_nhap.

SAI S1: đường module.dang_nhap lấy controller từ hook test
``_tao_trinh_duyet`` (mặc định None) mà production không có lời gọi
``dang_ky_trinh_duyet`` nào → ``trinh_duyet_none``/``can_nguoi`` mà chưa
từng mở trình duyệt. Mảnh này: lõi cấp controller trước khi gọi (một cách
duy nhất — truyền tham số ``trinh_duyet``, đúng mẫu tầng (b) của engine);
module giữ nguyên tiers/quyết định, chỉ nhận controller từ lõi.

Đỏ-trước-xanh: trước mảnh này, dang_nhap không nhận tham số trinh_duyet
(TypeError), bỏ qua factory đã đăng ký (can_nguoi), lõi không có helper
lấy controller cho tài khoản, và MCP không truyền controller vào
khoi_phuc_tu_dong.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import kho_tai_khoan
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach

THU_MUC_MODULE = Path("modules/grok-web")
HO_SO = "abcdef0123456789"
EMAIL = "ban-gia@example.com"


def chay(coro):
    return asyncio.run(coro)


def tai_grok_web():
    """Nạp modules/grok-web như lõi nạp (gói riêng, import tương đối chạy được)."""
    ten = "web2api_module_grok_web_nv309a"
    sys.modules.pop(ten, None)
    tep = THU_MUC_MODULE / "module.py"
    manifest = json.loads((THU_MUC_MODULE / "manifest.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        ten, tep, submodule_search_locations=[str(THU_MUC_MODULE)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod, manifest


def mod_moi(monkeypatch):
    mod, manifest = tai_grok_web()
    m = mod.Module(manifest)

    async def uid_gia(jar, goc):
        return "uid_1"

    monkeypatch.setattr(mod, "lay_uid_tu_phien", uid_gia)
    return m


def td_thanh_cong(url_dang_nhap):
    """Controller giả: mở form, không thử thách, cookie sso sẵn."""
    return TrinhDuyetGia(
        trang={url_dang_nhap: {"tin_hieu": [], "cookie": {"sso": "sso-gia"}}}
    )


class ModTrinhDuyetGia:
    """Module trình duyệt giả cho cầu nối lõi."""

    def __init__(self, td):
        self.manifest = {"cung_cap": ["trinh_duyet"]}
        self._td = td
        self.da_tao: list[str] = []
        self.da_dong: list[str] = []

    def trinh_duyet_that(self, ho_so):
        return self._td

    async def trinh_duyet_tao(self, ten):
        self.da_tao.append(ten)
        return {"ho_so": HO_SO, "ten": ten}

    async def trinh_duyet_dong(self, ho_so, giu_phien):
        self.da_dong.append((ho_so, giu_phien))
        return {}


def dong_mat_khau():
    return DongDaTach(dong=1, email=EMAIL, loai="mat_khau",
                      mat_khau=BiMatTam("mat-khau-gia"))


# ── Module: dang_nhap nhận controller từ lõi ──────────────────────────────

def test_dang_nhap_mat_khau_dung_trinh_duyet_truyen_vao(monkeypatch):
    m = mod_moi(monkeypatch)
    td = td_thanh_cong(m._URL_DANG_NHAP)
    kq = chay(m.dang_nhap(dong_mat_khau(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert ("mo_trang", m._URL_DANG_NHAP) in td.hanh_dong


def test_dang_nhap_google_tai_dung_ho_so_tra_cookie(monkeypatch):
    """Luật 17:52 — Grok xong email-direct rồi tới Google: đường Google tái
    dùng hồ sơ phải trả cookie phiên (chứng minh session ở mức test).
    Probe thử thách là HTTP ra ngoài nên stub False ở đây (không phải phần
    chứng minh của test này); selector nút Google thật vẫn chờ chủ máy chọn
    bằng tay qua noVNC ở vòng kiểm thật (xem báo cáo)."""
    m = mod_moi(monkeypatch)
    td = TrinhDuyetGia(
        trang={m._URL_TAI_DUNG: {"tin_hieu": ["da_dang_nhap"],
                                 "cookie": {"sso": "sso-gia"}}})

    async def khong_co_thu_thach(*a):
        return False

    monkeypatch.setattr(m, "_co_thu_thach", khong_co_thu_thach)
    dong = DongDaTach(dong=1, email=EMAIL, loai="google")
    dong.ho_so_google = HO_SO
    kq = chay(m.dang_nhap(dong, trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert "sso=sso-gia" in (kq.cookie or "")
    assert ("mo_trang", m._URL_TAI_DUNG) in td.hanh_dong


def test_dang_nhap_mat_khau_khong_co_trinh_duyet_thi_can_nguoi(monkeypatch):
    """Fail-closed giữ nguyên: không controller nào → can_nguoi, không vỡ."""
    m = mod_moi(monkeypatch)
    kq = chay(m.dang_nhap(dong_mat_khau()))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"


def test_dang_nhap_mat_khau_dung_factory_da_dang_ky(monkeypatch):
    """dang_nhap phải hỏi factory đã đăng ký, không chỉ hook test."""
    m = mod_moi(monkeypatch)
    td = td_thanh_cong(m._URL_DANG_NHAP)
    m.dang_ky_trinh_duyet(lambda: td)
    kq = chay(m.dang_nhap(dong_mat_khau()))
    assert kq.thanh_cong, kq.ma_loi
    assert ("mo_trang", m._URL_DANG_NHAP) in td.hanh_dong


def test_dang_nhap_mat_khau_uu_tien_trinh_duyet_truyen_vao(monkeypatch):
    """Tham số tường minh thắng factory (lõi cấp cho đúng lượt này)."""
    m = mod_moi(monkeypatch)
    td_loi = td_thanh_cong(m._URL_DANG_NHAP)
    td_factory = td_thanh_cong(m._URL_DANG_NHAP)
    m.dang_ky_trinh_duyet(lambda: td_factory)
    kq = chay(m.dang_nhap(dong_mat_khau(), trinh_duyet=td_loi))
    assert kq.thanh_cong
    assert ("mo_trang", m._URL_DANG_NHAP) in td_loi.hanh_dong
    assert td_factory.hanh_dong == []


# ── Lõi: helper lấy controller cho một tài khoản ───────────────────────────

def test_lay_trinh_duyet_cho_tai_khoan_co_ho_so():
    from app import khoi_phuc_phien as kp

    td = TrinhDuyetGia()
    dang_chay = {"vnc-xa": ModTrinhDuyetGia(td)}
    got_td, got_hs, da_tao = chay(
        kp.lay_trinh_duyet_cho_tai_khoan(dang_chay, ho_so=HO_SO))
    assert got_td is td and got_hs == HO_SO and da_tao is False


def test_lay_trinh_duyet_cho_tai_khoan_tao_workspace_moi():
    from app import khoi_phuc_phien as kp

    td = TrinhDuyetGia()
    mod_td = ModTrinhDuyetGia(td)
    dang_chay = {"vnc-xa": mod_td}
    got_td, got_hs, da_tao = chay(
        kp.lay_trinh_duyet_cho_tai_khoan(dang_chay, email=EMAIL))
    assert got_td is td and got_hs == HO_SO and da_tao is True
    assert mod_td.da_tao == [EMAIL]


def test_lay_trinh_duyet_cho_tai_khoan_khong_co_duong():
    from app import khoi_phuc_phien as kp

    got_td, got_hs, da_tao = chay(
        kp.lay_trinh_duyet_cho_tai_khoan({}, email=EMAIL))
    assert got_td is None and got_hs == "" and da_tao is False


def test_controller_chi_doc_cookie_mien_module_khai():
    from app import khoi_phuc_phien as kp

    td = TrinhDuyetGia(cookie_theo_mien={
        "gia.example": {"sid": "cookie-gia"},
        "other.example": {"sid": "cookie-khac"},
    })

    class ModuleDich:
        manifest = {}

        def quy_tac_tin_hieu(self):
            return [{"loai": "cookie", "mien": "gia.example",
                     "ten": "sid", "tin_hieu": "da_dang_nhap"}]

    module_dich = ModuleDich()
    dang_chay = {"vnc-xa": ModTrinhDuyetGia(td), "mod-gia": module_dich}
    controller, _, _ = chay(kp.lay_trinh_duyet_cho_tai_khoan(
        dang_chay, ho_so=HO_SO, module=module_dich))
    assert not hasattr(controller, "_td")
    assert not hasattr(controller, "_khoa")
    assert chay(controller.doc_cookie_ho_so_da_dong(
        "gia.example")) == {"sid": "cookie-gia"}
    with pytest.raises(PermissionError):
        chay(controller.doc_cookie_ho_so_da_dong("other.example"))


# ── NV144: truyền dang_chay → controller vào module.dang_nhap ──────────────

def _stub_kho_nv144(monkeypatch):
    import contextlib

    from app import kho_tai_khoan as ktk

    monkeypatch.setattr(ktk, "tu_dang_nhap_lai_bat", lambda: True)
    monkeypatch.setattr(ktk, "_con_het_han_muc", lambda _id: False)
    monkeypatch.setattr(ktk, "_doc_trang_thai", lambda: {})
    monkeypatch.setattr(ktk, "_ghi_trang_thai", lambda _tt: None)
    monkeypatch.setattr(ktk, "_doc", lambda: {})
    monkeypatch.setattr(ktk.kho, "giu_khoa_kho",
                        lambda: contextlib.nullcontext())
    monkeypatch.setattr(ktk, "ghi_ket_qua_dang_nhap_tu_ket_qua",
                        lambda _id, _kq: None)
    monkeypatch.setattr(ktk, "tim_theo_email",
                        lambda _email, _mid, _nhom: {
                            "id": "tk-1", "email": EMAIL, "mat_khau": "x",
                            "cap_nhat": 0,
                            kho_tai_khoan.TRUONG_HO_SO: HO_SO})
    monkeypatch.setattr(ktk, "lay_bi_mat_dang_nhap",
                        lambda _id: (BiMatTam("mat-khau-gia"), None))


def test_nv144_truyen_trinh_duyet_khi_co_dang_chay(monkeypatch):
    from app import kho_tai_khoan as ktk
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    _stub_kho_nv144(monkeypatch)
    td = TrinhDuyetGia()
    ghi = {}

    class ModGia:
        manifest = {}

        async def dang_nhap(self, dong, trinh_duyet=None):
            ghi["trinh_duyet"] = trinh_duyet
            return KetQuaDangNhap(True, cookie="ck-gia")

    kq = chay(ktk.thu_dang_nhap_lai(
        module=ModGia(), module_id="grok-web", dinh_danh=EMAIL,
        dang_chay={"vnc-xa": ModTrinhDuyetGia(td)}))
    assert kq == {"thu": True, "thanh_cong": True}
    assert ghi["trinh_duyet"] is td


def test_nv144_khong_co_dang_chay_giu_loi_goi_cu(monkeypatch):
    """Không có dang_chay → hành vi cũ, module kiểu cũ vẫn chạy được."""
    from app import kho_tai_khoan as ktk
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    _stub_kho_nv144(monkeypatch)

    class ModCu:
        manifest = {}

        async def dang_nhap(self, dong):
            return KetQuaDangNhap(True, cookie="ck-gia")

    kq = chay(ktk.thu_dang_nhap_lai(
        module=ModCu(), module_id="grok-web", dinh_danh=EMAIL))
    assert kq == {"thu": True, "thanh_cong": True}


# ── MCP: truyền controller vào khoi_phuc_tu_dong ───────────────────────────

def test_mcp_truyen_trinh_duyet_vao_khoi_phuc(monkeypatch):
    from app import cong_mcp_cong_cu as mcp

    td = TrinhDuyetGia()
    mod_td = ModTrinhDuyetGia(td)
    ghi = {}

    class ModGia:
        async def khoi_phuc_tu_dong(self, khoa, email="", nghi_t1_giay=0.0,
                                    trinh_duyet=None, **kw):
            ghi["trinh_duyet"] = trinh_duyet
            ghi["khoa"] = khoa
            return {"thanh_cong": True, "ma_loi": "", "can_nguoi": False,
                    "huong_dan": ""}

    truong_hs = kho_tai_khoan.TRUONG_HO_SO
    monkeypatch.setattr(
        kho_tai_khoan, "tim_theo_id",
        lambda tk_id: {"email": EMAIL, truong_hs: HO_SO})
    so = SimpleNamespace(dang_chay={"vnc-xa": mod_td, "grok-web": ModGia()})
    kq = chay(mcp._thu_khoi_phuc_module(so, "tk-1", "grok-web"))
    assert kq is not None and kq["thanh_cong"] is True
    assert ghi["trinh_duyet"] is td


def test_mcp_kiem_lai_quyen_truoc_khi_cap_controller(monkeypatch):
    from app import cong_mcp_cong_cu as mcp
    from app import khoi_phuc_phien as kp
    from app.cong_mcp_cong_cu import LoiCongCu

    monkeypatch.setenv("WEB2API_MCP_DANG_NHAP_CHO_PHEP", "tk-1@mod-gia")
    ban_ghi_cu = {"id": "tk-1", "module_id": "mod-gia", "email": EMAIL,
                  kho_tai_khoan.TRUONG_HO_SO: HO_SO}
    ban_ghi_moi = {**ban_ghi_cu, "module_id": "module-da-doi"}
    doc = iter([ban_ghi_cu, ban_ghi_moi])
    monkeypatch.setattr(kho_tai_khoan, "tim_theo_id", lambda _id: next(doc))
    monkeypatch.setattr(kho_tai_khoan, "da_tam_dung", lambda _id: False)
    td = TrinhDuyetGia()

    async def lay(*args, **kwargs):
        return td, HO_SO, False

    monkeypatch.setattr(kp, "lay_trinh_duyet_cho_tai_khoan", lay)
    da_goi = []

    class ModGia:
        manifest = {"cung_cap": ["trinh_duyet"]}

        def quy_tac_tin_hieu(self):
            return [{"loai": "cookie", "mien": "gia.example",
                     "ten": "phien", "tin_hieu": "da_dang_nhap"}]

        async def khoi_phuc_tu_dong(self, *args, trinh_duyet=None, **kwargs):
            da_goi.append(trinh_duyet)
            return {"thanh_cong": True}

    so = SimpleNamespace(dang_chay={"mod-gia": ModGia()})
    with pytest.raises(LoiCongCu) as loi:
        chay(mcp._thu_khoi_phuc_module(so, "tk-1", "mod-gia",
                                       kiem_quyen=True))
    assert loi.value.ma == "khong_cho_phep"
    assert da_goi == []
