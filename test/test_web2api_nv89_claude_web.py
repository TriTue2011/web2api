"""Test NV89 cho module claude-web: đăng nhập tự động + khôi phục.

- `dang_nhap(DongDaTach)` theo hợp đồng NV67 (bộ chạy lần lượt tái dùng).
- `lam_moi_phien` (tầng a), `dang_nhap_tu_dong` (tầng b), `tai_khoan_ke`/
  `danh_dau_hong` (tầng c), `mat_khau_da_luu`, `co_tai_khoan`, `ho_so_trinh_duyet`.
- Trình duyệt là GIẢ (TrinhDuyetGia của app.khoi_phuc_phien); HTTP là máy chủ
  claude.ai giả (httpx.MockTransport chích vào MayKhachClaudeWeb._khach_http —
  đúng pattern mock máy chủ giả của test claude-web hiện có, nhưng ở tầng
  transport nên không cần loopback). Không Internet, không Chrome.

Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import httpx
import pytest
import re

from app import kho
from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc, TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "claude-web"
TEN_GOI = "claude_web_nv89_mod_test"
URL_DANG_NHAP = "https://claude.ai/login"

# Cookie giả: máy chủ giả nhận diện theo chuỗi con trong header Cookie.
COOKIE_GIA_OK = "sessionKey=sessionKey-gia-nv89-ok"
COOKIE_GIA_HET_HAN = "sessionKey=sessionKey-gia-nv89-het-han"
COOKIE_GIA_LOI_LA = "sessionKey=sessionKey-gia-nv89-loi-la"
PHIEN_MOI_TU_TRINH_DUYET = "sessionKey-gia-nv89-ok"
MAT_KHAU_GIA = "mat-khau-gia-nv89-001"
EMAIL_GIA = "ban-gia-nv89@example.com"


def _tai_goi():
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
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


def _may_chu_gia():
    """Máy chủ claude.ai giả: cookie chứa sessionKey-gia-nv89-ok thì 200 kèm
    tổ chức có capability «chat»; ngược lại 401. Chuỗi con «loi-la» ném
    RuntimeError để kiểm fail-closed."""
    def xu_ly(req):
        cookie = req.headers.get("cookie", "")
        if req.url.path == "/api/organizations":
            if "sessionKey-gia-nv89-loi-la" in cookie:
                raise RuntimeError("loi-la-gia-nv89")
            if "sessionKey-gia-nv89-ok" in cookie:
                return httpx.Response(200, json=[{"uuid": "org-gia-1",
                                                  "capabilities": ["chat"]}])
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")
    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch):
    """Module claude-web với máy chủ giả chích qua _khach_http + kho bí mật tạm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("claude-web")
    transport = _may_chu_gia()

    def khach_http(self):
        return httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(mod_client.MayKhachClaudeWeb, "_khach_http", khach_http)
    return mod


def _trinh_duyet_thanh_cong(phien=PHIEN_MOI_TU_TRINH_DUYET):
    return TrinhDuyetGia(trang={
        URL_DANG_NHAP: {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sessionKey": phien},
        }})


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


# ───────────── dang_nhap: dán cookie ─────────────

def test_dang_nhap_cookie_hop_le(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_OK))
    kq = chay(mod.dang_nhap(tk))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == COOKIE_GIA_OK
    assert tk.cookie.da_xoa()
    assert mod.co_tai_khoan(mod._khoa_tu_cookie(COOKIE_GIA_OK))


def test_dang_nhap_cookie_het_han(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_HET_HAN))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong
    assert kq.ma_trang_thai == 401
    assert tk.cookie.da_xoa()


# ───────────── dang_nhap: mật khẩu qua trình duyệt giả ─────────────

def test_dang_nhap_mat_khau_thanh_cong_va_luu_mat_khau(mod):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert tk.mat_khau.da_xoa()
    # mật khẩu đã lưu NHƯ BÍ MẬT để tầng (b) dùng lại sau
    khoa = mod._khoa_tu_cookie(kq.cookie)
    assert khoa.startswith("cl-") and len(khoa) == 15
    bm = mod.mat_khau_da_luu(khoa)
    assert bm is not None and bm.lay() == MAT_KHAU_GIA
    bm.xoa()
    # thứ tự bước đăng nhập trên trình duyệt giả
    ten_buoc = [h[0] for h in mod._trinh_duyet_da_dung.hanh_dong]
    assert ten_buoc[0] == "mo_trang"
    assert mod._trinh_duyet_da_dung.hanh_dong[0] == ("mo_trang", URL_DANG_NHAP)
    assert "dien" in ten_buoc and "bam" in ten_buoc


def test_dang_nhap_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        URL_DANG_NHAP: {"tin_hieu": ["captcha"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()


def test_dang_nhap_khong_co_trinh_duyet_thi_can_nguoi(mod):
    """Không có bộ điều khiển trình duyệt → không đoán mò, báo cần người."""
    mod._tao_trinh_duyet = lambda: None
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"


def test_dang_nhap_khong_lay_duoc_phien(mod):
    """Trình duyệt xong nhưng không có cookie sessionKey → thất bại rõ mã."""
    td = TrinhDuyetGia(trang={
        URL_DANG_NHAP: {"tin_hieu": ["thanh_cong"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "khong_lay_duoc_phien"


def test_dang_nhap_phien_khong_dung_duoc(mod):
    """Cookie sessionKey đọc được nhưng claude.ai từ chối → 401, mật khẩu đã xoá."""
    mod._tao_trinh_duyet = lambda: _trinh_duyet_thanh_cong("sessionKey-gia-nv89-het-han")
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong
    assert kq.ma_loi == "phien_khong_dung_duoc"
    assert kq.ma_trang_thai == 401
    assert tk.mat_khau.da_xoa()


def test_dang_nhap_lai_dung_mat_khau_da_luu(mod):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq1 = chay(mod.dang_nhap(tk))
    assert kq1.thanh_cong
    # dòng chỉ có email → đăng nhập lại bằng mật khẩu đã lưu, không cần gõ lại
    tk2 = DongDaTach(dong=2, email=EMAIL_GIA, loai="dang_nhap_lai")
    kq2 = chay(mod.dang_nhap(tk2))
    assert kq2.thanh_cong, kq2.ma_loi


def test_dang_nhap_lai_khong_co_mat_khau_luu(mod):
    tk = DongDaTach(dong=1, email="chua-luu@example.com", loai="dang_nhap_lai")
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "khong_co_mat_khau_luu"


# ───────────── tầng (a): làm mới phiên ─────────────

def test_lam_moi_phien_thanh_cong(mod):
    mod._them_cookie(COOKIE_GIA_OK)
    khoa = mod._khoa_tu_cookie(COOKIE_GIA_OK)
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and kq.tang == "a"


def test_lam_moi_phien_het_han(mod):
    mod._them_cookie(COOKIE_GIA_HET_HAN)
    khoa = mod._khoa_tu_cookie(COOKIE_GIA_HET_HAN)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong
    assert kq.ma_loi == "het_han" and kq.ma_trang_thai == 401
    assert kq.tang == "a"


def test_lam_moi_phien_khoa_la(mod):
    kq = chay(mod.lam_moi_phien("cl-khong-co"))
    assert not kq.thanh_cong and kq.ma_loi == "khoa_khong_co"


# ───────────── tầng (b): đăng nhập lại tự động ─────────────

def test_dang_nhap_tu_dong(mod):
    td = _trinh_duyet_thanh_cong()
    tk = TaiKhoanCanKhoiPhuc(khoa="cl-moi", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, td))
    assert kq.thanh_cong and kq.tang == "b"
    assert tk.mat_khau.da_xoa()  # module xoá sau khi đọc xong


def test_dang_nhap_tu_dong_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        URL_DANG_NHAP: {"tin_hieu": ["2fa"], "cookie": {}}})
    tk = TaiKhoanCanKhoiPhuc(khoa="cl-moi", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, td))
    assert not kq.thanh_cong and kq.can_nguoi
    assert tk.mat_khau.da_xoa()


# ───────────── tầng (c): chuyển tài khoản kế ─────────────

def test_tai_khoan_ke_va_danh_dau_hong(mod):
    mod._them_cookie(COOKIE_GIA_OK)
    mod._them_cookie(COOKIE_GIA_HET_HAN)
    khoa_ok = mod._khoa_tu_cookie(COOKIE_GIA_OK)
    khoa_hh = mod._khoa_tu_cookie(COOKIE_GIA_HET_HAN)
    mod.danh_dau_hong(khoa_hh, "sai_chung_thuc")
    # tài khoản hỏng bị bỏ qua → kế của nó là tài khoản khoẻ
    assert mod.tai_khoan_ke(khoa_hh) == khoa_ok


def test_chi_mot_tai_khoan_thi_khong_co_ke(mod):
    mod._them_cookie(COOKIE_GIA_OK)
    khoa = mod._khoa_tu_cookie(COOKIE_GIA_OK)
    assert mod.tai_khoan_ke(khoa) is None


def test_ho_so_trinh_duyet_hop_khuon(mod):
    hs = mod.ho_so_trinh_duyet("cl-a1b2c3d4e5f6")
    assert hs.startswith("claude-web-")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", hs)


# ───────────── tái dùng bộ chạy lần lượt ─────────────

def test_luot_chay_voi_dang_nhap_cua_module(mod):
    """LuotChay (NV67) gọi thẳng mod.dang_nhap — lõi tái dùng, không viết lại."""
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    ds = [
        DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie", cookie=BiMatTam(COOKIE_GIA_OK)),
        DongDaTach(dong=2, email="hai@example.com", loai="mat_khau",
                   mat_khau=BiMatTam(MAT_KHAU_GIA)),
    ]
    luot = LuotChay(ds, mod.dang_nhap, ngu=lambda s: asyncio.sleep(0))
    tom_tat = chay(luot.chay())
    assert (tom_tat.xong, tom_tat.loi) == (2, 0)
    for tk in ds:
        tk.xoa_bi_mat()


# ───────────── bí mật không vào log ─────────────

def test_mat_khau_va_cookie_khong_vao_log(mod, caplog):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    with caplog.at_level(logging.INFO, logger="web2api.module.claude-web"):
        chay(mod.dang_nhap(tk))
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU_GIA not in ban_log
    assert "sessionKey-gia-nv89-ok" not in ban_log


# ───────────── fail-closed: lỗi lạ đóng cửa ─────────────

def test_loi_la_dang_nhap_dong_cua(mod):
    """lay_org_id ném RuntimeError → kết quả THẤT BẠI, không bao giờ True."""
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_LOI_LA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong
    assert kq.ma_loi == "loi_mang"
    assert tk.cookie.da_xoa()


def test_loi_la_lam_moi_phien_dong_cua(mod):
    mod._them_cookie(COOKIE_GIA_LOI_LA)
    khoa = mod._khoa_tu_cookie(COOKIE_GIA_LOI_LA)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_loi == "loi_mang"


# ───────────── URL qua app/tu_xa.py (chống SSRF) ─────────────

def test_cai_base_url_loopback_bi_chan_khi_chua_cho_phep(mod, monkeypatch):
    """base_url trỏ loopback mà test không cho phép → cai() từ chối ngay ở cổng
    kiểm URL (app/tu_xa.py), không gọi HTTP."""
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    kq = chay(mod.cai({"cookies": [COOKIE_GIA_OK],
                       "base_url": "http://127.0.0.1:9"}))
    assert kq["ok"] is False
    assert "không hợp lệ" in kq["loi"]


def test_cai_base_url_mac_dinh_khong_can_kiem_tu_xa(mod):
    """Không ghi đè base_url → dùng hằng số claude.ai, cai() đi thẳng tới bước
    kiểm cookie qua máy chủ giả (không cần Internet/DNS)."""
    kq = chay(mod.cai({"cookies": [COOKIE_GIA_OK]}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["cookies"] == [COOKIE_GIA_OK]
