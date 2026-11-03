"""NV300 — mỗi tài khoản một cách đăng nhập (google / email), chạy cùng lúc.

Test ĐỎ-TRƯỚC-XANH: viết trước khi sửa mã, chạy trên mã cũ phải ĐỎ
(thiếu hàm/tham số hoặc đi sai đường), sau khi sửa phải XANH.
Trình duyệt GIẢ, máy chủ GIẢ, không Internet, không Chrome.
Mọi chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.

Phạm vi:
- Kho tài khoản (app/kho_tai_khoan.py): trường cach_dang_nhap ∈
  {google, email} + ho_so_google, suy mặc định từ dữ liệu cũ, bản
  công khai có hai trường mới, chế độ google không cần mật khẩu.
- grok-web: HAI tài khoản trong CÙNG module, một google một email,
  mỗi cái đi đúng đường của mình dù cài đặt chung cũ là truc_tiep;
  cài đặt chung cũ chỉ còn làm mặc định đọc ngược.
- claude-web: tạm thời CHỈ google — truyền cach=email vẫn đi đường
  google, không đi đường mật khẩu.
- chatgpt-web: truyền cach=google thì đi đường hồ sơ, không đòi mật khẩu.
- Route: lưu tài khoản chế độ google không cần mật khẩu.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from app import kho, kho_tai_khoan
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach

GOC = Path(__file__).resolve().parents[1]

EMAIL_GIA = "ban-gia-nv300@example.com"
MAT_KHAU_GIA = "mat-khau-gia-nv300-001"
SSO_GIA_OK = "sso-gia-nv300-ok"
UID_GIA = "uid-gia-nv300"


def chay(coro):
    return asyncio.run(coro)


def _tai_goi_module(thu_muc: Path, ten_goi: str):
    for ten in [t for t in list(sys.modules)
                if t == ten_goi or t.startswith(ten_goi + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(ten_goi)
    goi.__path__ = [str(thu_muc)]
    sys.modules[ten_goi] = goi
    for ten_tep in ("client", "converter", "module"):
        tep = thu_muc / f"{ten_tep}.py"
        if not tep.exists():
            continue
        spec = importlib.util.spec_from_file_location(
            f"{ten_goi}.{ten_tep}", tep)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{ten_goi}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{ten_goi}.module"]


# ── Kho tài khoản ─────────────────────────────────────────────────────────

def test_kiem_cach_dang_nhap():
    assert kho_tai_khoan.kiem_cach_dang_nhap("google") == "google"
    assert kho_tai_khoan.kiem_cach_dang_nhap("email") == "email"
    assert kho_tai_khoan.kiem_cach_dang_nhap(None) == ""
    assert kho_tai_khoan.kiem_cach_dang_nhap("") == ""
    assert kho_tai_khoan.kiem_cach_dang_nhap("  GOOGLE ") == "google"
    with pytest.raises(kho_tai_khoan.LoiKhoTaiKhoan):
        kho_tai_khoan.kiem_cach_dang_nhap("sai")


def test_cach_dang_nhap_cua_suy_tu_du_lieu_cu():
    # Đã ghi rõ → dùng giá trị đã ghi.
    assert kho_tai_khoan.cach_dang_nhap_cua(
        {"cach_dang_nhap": "google"}) == "google"
    assert kho_tai_khoan.cach_dang_nhap_cua(
        {"cach_dang_nhap": "email"}) == "email"
    # Chưa ghi: liên kết đăng nhập hộ NV223 KHÔNG suy thành google —
    # đó vẫn là đường mật khẩu (bí mật của tài khoản Google), khác với
    # tái dùng hồ sơ của NV300; bản ghi có mật khẩu riêng → email.
    assert kho_tai_khoan.cach_dang_nhap_cua(
        {"dang_nhap_bang": "google",
         "tai_khoan_dnh_id": "tk-gia-google",
         "mat_khau": "mat-khau-gia"}) == "email"
    # Chưa ghi, chỉ có liên kết hộ mà không có mật khẩu → rỗng (module
    # giữ hành vi cũ), không đoán thành google.
    assert kho_tai_khoan.cach_dang_nhap_cua(
        {"dang_nhap_bang": "google",
         "tai_khoan_dnh_id": "tk-gia-google"}) == ""
    # Chưa ghi: có mật khẩu → email.
    assert kho_tai_khoan.cach_dang_nhap_cua(
        {"mat_khau": "mat-khau-gia"}) == "email"
    assert kho_tai_khoan.cach_dang_nhap_cua(
        {"bi_mat": "bi-mat-gia", "kieu_bi_mat": "mat_khau"}) == "email"
    # Chưa ghi, không có gì → rỗng.
    assert kho_tai_khoan.cach_dang_nhap_cua({}) == ""


def test_luu_tai_khoan_ghi_cach_va_ho_so(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    # Chế độ google: không cần mật khẩu, nhưng cần id hồ sơ Google.
    bk = kho_tai_khoan.luu_tai_khoan(
        "gg-gia-nv300@example.com", None, None, "",
        cach_dang_nhap="google", ho_so="tk-gia-google-1")
    assert bk["cach_dang_nhap"] == "google"
    assert bk["ho_so_google"] == "tk-gia-google-1"
    # Google mà thiếu hồ sơ → từ chối, không lưu dở.
    with pytest.raises(kho_tai_khoan.LoiKhoTaiKhoan):
        kho_tai_khoan.luu_tai_khoan(
            "gg-thieu-ho-so@example.com", None, None, "",
            cach_dang_nhap="google")
    # Chế độ email: cần mật khẩu như cũ.
    bk2 = kho_tai_khoan.luu_tai_khoan(
        "email-gia-nv300@example.com", MAT_KHAU_GIA, None, "",
        cach_dang_nhap="email")
    assert bk2["cach_dang_nhap"] == "email"
    # Cập nhật không truyền cach → giữ nguyên cũ.
    bk3 = kho_tai_khoan.luu_tai_khoan(
        "email-gia-nv300@example.com", None, None, "Nhãn mới")
    assert bk3["cach_dang_nhap"] == "email"
    # Bản ghi cũ chưa có trường vẫn đọc được qua suy luận.
    cu = kho_tai_khoan.tim_theo_email("email-gia-nv300@example.com")
    assert kho_tai_khoan.cach_dang_nhap_cua(cu) == "email"


# ── grok-web: hai tài khoản, hai đường, cùng một module ──────────────────

def _mod_grok(tmp_path, monkeypatch):
    thu_muc = GOC / "modules" / "grok-web"
    mod_module = _tai_goi_module(thu_muc, "grok_web_module_nv300_test")
    manifest = json.loads((thu_muc / "manifest.json").read_text(
        encoding="utf-8"))
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = mod_module.Module(dict(manifest))
    m.kho = kho.kho_rieng("grok-web")
    return m


def test_grok_hai_tai_khoan_moi_duong(tmp_path, monkeypatch):
    """Tài khoản google đi đường hồ sơ, tài khoản email đi đường mật khẩu,
    trong CÙNG module mà cài đặt chung cũ vẫn là truc_tiep."""
    m = _mod_grok(tmp_path, monkeypatch)
    assert m._che_do_dang_nhap == "truc_tiep"
    # Tài khoản google: đặt cách theo từng tài khoản, không cần mật khẩu.
    m.dat_cach_dang_nhap("tk-gg-nv300", "google", "ho-so-gia-1")
    td_gg = TrinhDuyetGia(trang={
        "https://grok.com/": {"tin_hieu": ["da_dang_nhap"],
                              "cookie": {"sso": SSO_GIA_OK,
                                         "x-userid": UID_GIA}}})
    kq_gg = chay(m.khoi_phuc_tu_dong("tk-gg-nv300", EMAIL_GIA,
                                     trinh_duyet=td_gg))
    assert kq_gg.thanh_cong, kq_gg.ma_loi
    assert ("mo_trang", "https://grok.com/") in td_gg.hanh_dong
    assert ("dien", "email") not in td_gg.hanh_dong
    assert ("dien", "mat_khau") not in td_gg.hanh_dong
    # Tài khoản email: mật khẩu đã lưu, đi form x.com/login.
    m._luu_mat_khau("tk-email-nv300", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    m.dat_cach_dang_nhap("tk-email-nv300", "email")
    td_email = TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": ["thanh_cong"],
                                "cookie": {"sso": SSO_GIA_OK,
                                           "x-userid": UID_GIA}}})
    kq_email = chay(m.khoi_phuc_tu_dong("tk-email-nv300", EMAIL_GIA,
                                        trinh_duyet=td_email))
    assert kq_email.thanh_cong, kq_email.ma_loi
    assert ("dien", "email") in td_email.hanh_dong
    assert ("dien", "mat_khau") in td_email.hanh_dong


def test_grok_che_do_cu_chi_lam_mac_dinh_doc_nguoc(tmp_path, monkeypatch):
    """Tài khoản chưa có cách riêng thì đọc ngược cài đặt chung cũ."""
    m = _mod_grok(tmp_path, monkeypatch)
    m._nap_tu({"tai_khoan": [f"sso={SSO_GIA_OK}"],
               "che_do_dang_nhap": "google"})
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {"tin_hieu": ["da_dang_nhap"],
                              "cookie": {"sso": SSO_GIA_OK,
                                         "x-userid": UID_GIA}}})
    kq = chay(m.khoi_phuc_tu_dong("tk-cu-nv300", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert ("dien", "email") not in td.hanh_dong


def test_grok_dang_nhap_loai_google(tmp_path, monkeypatch):
    m = _mod_grok(tmp_path, monkeypatch)
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {"tin_hieu": ["da_dang_nhap"],
                              "cookie": {"sso": SSO_GIA_OK,
                                         "x-userid": UID_GIA}}})
    m._tao_trinh_duyet = lambda: td
    dong = DongDaTach(dong=1, email=EMAIL_GIA, loai="google")
    dong.ho_so_google = "ho-so-gia-1"
    kq = chay(m.dang_nhap(dong))
    assert kq.thanh_cong, kq.ma_loi
    assert ("dien", "email") not in td.hanh_dong


# ── claude-web: tạm thời chỉ google ───────────────────────────────────────

def _mod_claude(tmp_path, monkeypatch):
    thu_muc = GOC / "modules" / "claude-web"
    mod_module, mod_client = None, None
    for ten in [t for t in list(sys.modules)
                if t == "claude_web_module_nv300_test"
                or t.startswith("claude_web_module_nv300_test.")]:
        del sys.modules[ten]
    goi = types.ModuleType("claude_web_module_nv300_test")
    goi.__path__ = [str(thu_muc)]
    sys.modules["claude_web_module_nv300_test"] = goi
    for ten_tep in ("client", "converter", "module"):
        tep = thu_muc / f"{ten_tep}.py"
        if not tep.exists():
            continue
        spec = importlib.util.spec_from_file_location(
            f"claude_web_module_nv300_test.{ten_tep}", tep)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"claude_web_module_nv300_test.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    mod_module = sys.modules["claude_web_module_nv300_test.module"]
    mod_client = sys.modules["claude_web_module_nv300_test.client"]
    manifest = json.loads((thu_muc / "manifest.json").read_text(
        encoding="utf-8"))
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = mod_module.Module(dict(manifest))
    m.kho = kho.kho_rieng("claude-web")

    def xu_ly(req):
        cookie = req.headers.get("cookie", "")
        if req.url.path == "/api/organizations":
            if "sessionKey-gia-claude-ok" in cookie:
                return httpx.Response(200, json=[{"uuid": "org-gia-1",
                                                  "capabilities": ["chat"]}])
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")

    transport = httpx.MockTransport(xu_ly)

    # Module kiểm phiên qua client NHẸ ClaudeWebClient (nhận transport),
    # không qua MayKhachClaudeWeb — chích transport vào _tao_client.
    def tao_client(self, session_key):
        return mod_client.ClaudeWebClient(session_key, base_url=m._base_url,
                                          transport=transport)
    monkeypatch.setattr(mod_module.Module, "_tao_client", tao_client)
    return m


def test_claude_chi_google_truyen_email_van_di_duong_google(tmp_path,
                                                            monkeypatch):
    """Bổ sung NV300: tài khoản Claude luôn google — kể cả khi người gọi
    truyền cach=email, khôi phục vẫn đi đường hồ sơ, không đòi mật khẩu."""
    m = _mod_claude(tmp_path, monkeypatch)
    td = TrinhDuyetGia(trang={
        "https://claude.ai/": {"tin_hieu": ["da_dang_nhap"],
                               "cookie": {"sessionKey":
                                          "sessionKey-gia-claude-ok"}}})
    kq = chay(m.khoi_phuc_tu_dong("tk-claude-nv300", EMAIL_GIA,
                                  trinh_duyet=td,
                                  cach_dang_nhap="email"))
    assert kq.thanh_cong, kq.ma_loi
    assert ("dien", "email") not in td.hanh_dong
    assert ("dien", "mat_khau") not in td.hanh_dong


# ── chatgpt-web: cach=google thì không đòi mật khẩu ───────────────────────

def test_chatgpt_cach_google_khong_doi_mat_khau(tmp_path, monkeypatch):
    thu_muc = GOC / "modules" / "chatgpt-web"
    mod_module = _tai_goi_module(thu_muc, "chatgpt_web_module_nv300_test")
    manifest = json.loads((thu_muc / "manifest.json").read_text(
        encoding="utf-8"))
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = mod_module.Module(dict(manifest))
    m.kho = kho.kho_rieng("chatgpt-web")
    # Hồ sơ gặp thử thách → đường google trả can_nguoi NGAY (không sleep,
    # không mạng); quan trọng là KHÔNG về thieu_mat_khau của đường email.
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {"tin_hieu": ["captcha"], "cookie": {}}})
    # _URL_TAI_DUNG của module có thể khác; đặt trang captcha cho mọi URL
    # mở đầu bằng tên miền chính bằng cách mở trước trang đó.
    kq = chay(m.khoi_phuc_tu_dong("tk-chatgpt-nv300", EMAIL_GIA,
                                  trinh_duyet=td,
                                  cach_dang_nhap="google"))
    assert not kq.thanh_cong
    assert kq.ma_loi != "thieu_mat_khau"


# ── Route: lưu tài khoản chế độ google không cần mật khẩu ────────────────

def test_route_luu_tai_khoan_google_khong_can_mat_khau(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    khoa_qt = "khoa-qt-nv300-0123456789abcdef"
    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", khoa_qt)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        h = {"Authorization": f"Bearer {khoa_qt}"}
        r_gg = client.post("/api/tai-khoan/da-luu", headers=h, json={
            "email": "google-gia-nv300@example.com",
            "mat_khau": MAT_KHAU_GIA})
        assert r_gg.status_code == 200, r_gg.text
        id_gg = r_gg.json()["tai_khoan"]["id"]
        r = client.post("/api/tai-khoan/da-luu", headers=h, json={
            "email": "dich-vu-gia-nv300@example.com",
            "cach_dang_nhap": "google",
            "ho_so_google": id_gg})
        assert r.status_code == 200, r.text
        tk = r.json()["tai_khoan"]
        assert tk["cach_dang_nhap"] == "google"
        assert tk["ho_so_google"] == id_gg
        # Bí danh `ho_so` (khung gửi) cũng được chấp nhận như khóa chuẩn.
        r2 = client.post("/api/tai-khoan/da-luu", headers=h, json={
            "email": "dich-vu-gia-2-nv300@example.com",
            "cach_dang_nhap": "google",
            "ho_so": id_gg})
        assert r2.status_code == 200, r2.text
        assert r2.json()["tai_khoan"]["ho_so_google"] == id_gg
