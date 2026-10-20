"""Test module openaicodex (NV173) — toàn bộ chạy trên máy chủ OAuth GIẢ.

Không gọi mạng thật: mọi lời gọi HTTP đi qua hàm giả chích vào module
(`module._gan_http_gia`) hoặc truyền thẳng vào hàm của oauth.py/han_muc.py.
Chứng minh các hàng rào an toàn của brief:
- PKCE S256 đúng; state ngẫu nhiên, dùng MỘT lần, có hạn.
- redirect_uri khớp tuyệt đối; URL callback lạ (host không loopback) bị từ chối.
- Token KHÔNG vào phản hồi/suc_khoe/thuc_the/log; lưu qua kho riêng (0600).
- Làm mới: khoá theo refresh_token (một POST cho nhiều gọi trùng), phân loại
  lỗi theo CẤU TRÚC mã của nhà cung cấp (không dò chữ), lỗi mạng tạm không
  đánh dấu hỏng; account_deactivated → trạng thái deactivated + chờ duyệt.
- Hạn mức: đọc header x-codex-* và /wham/usage; kho khoá bằng băm ĐẦY ĐỦ của
  token (không cắt 40 ký tự như bản lỗi ở c2a).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import sys
import threading
import time
import types
from pathlib import Path

import httpx
import pytest

from app.kham_pha import kiem_chuan_module, kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "openaicodex"
TEN_GOI = "openai_codex_mod_test"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("oauth", "han_muc", "hoi_phuc", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.oauth"],
            sys.modules[f"{TEN_GOI}.han_muc"],
            sys.modules[f"{TEN_GOI}.hoi_phuc"])


mod_module, mod_oauth, mod_hanmuc, mod_hoiphuc = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Giả lập HTTP + JWT
# ---------------------------------------------------------------------------

class PhanHoiGia:
    def __init__(self, ma=200, json_=None, text=None, headers=None):
        self.status_code = ma
        self._json = json_
        self.text = text if text is not None else (
            json.dumps(json_) if json_ is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("không phải JSON")
        return self._json


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(d).encode()).rstrip(b"=").decode()


def tao_jwt(email="nguoi.a@example.com", plan="plus", het_sau_giay=8 * 3600,
            account_id="acc-123"):
    """JWT giả đúng cấu trúc claim Codex (chưa ký — module chỉ đọc claim)."""
    payload = {
        "email": email,
        "exp": int(time.time()) + het_sau_giay,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
        },
        "https://api.openai.com/profile": {"email": email},
    }
    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.chu-ky-gia"


class HttpGia:
    """http(method, url, data=..., headers=...) → PhanHoiGia; ghi lại lời gọi."""

    def __init__(self, xu_ly):
        self.xu_ly = xu_ly
        self.loi_goi: list[dict] = []
        self._khoa = threading.Lock()

    async def __call__(self, method, url, **kw):
        with self._khoa:
            self.loi_goi.append({"method": method, "url": url, **kw})
        return self.xu_ly(method, url, **kw)

    def dem(self, mau_url: str) -> int:
        return sum(1 for g in self.loi_goi if mau_url in g["url"])


def http_token_ok(access=None, refresh="refresh-moi"):
    """Máy chủ token giả: luôn cấp token mới."""
    access = access or tao_jwt()

    def xu_ly(method, url, **kw):
        return PhanHoiGia(200, json_={
            "access_token": access, "refresh_token": refresh,
            "expires_in": 28800, "token_type": "Bearer"})
    return HttpGia(xu_ly)


@pytest.fixture
def module(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod_oauth.don_bo_dem_lam_moi()  # cache làm mới là của tiến trình — dọn sạch
    from app import kho
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("openaicodex")
    return m


def _transport_token_ok(access=None, refresh="refresh-moi", dem=None):
    """Transport giả cho endpoint token: luôn cấp token mới."""
    access = access or tao_jwt()

    def xu_ly(req: httpx.Request) -> httpx.Response:
        if dem is not None:
            dem.append(str(req.url))
        return httpx.Response(200, json={
            "access_token": access, "refresh_token": refresh,
            "expires_in": 28800, "token_type": "Bearer"})
    return httpx.MockTransport(xu_ly)


# ---------------------------------------------------------------------------
# 1. Manifest + chuẩn module
# ---------------------------------------------------------------------------

def test_manifest_hop_le_va_dung_chuan():
    kiem_manifest(dict(MANIFEST))  # không ném = hợp lệ
    loi = kiem_chuan_module(MANIFEST["cung_cap"], Module)
    assert loi == {}, f"thiếu phương thức chuẩn: {loi}"
    assert MANIFEST["id"] == "openaicodex"
    # Mọi trường bí mật phải có mo_ta nói rõ lấy ở đâu / tự đặt thế nào.
    for t in MANIFEST["truong_cau_hinh"]:
        if t["kieu"] in ("bi_mat", "danh_sach_bi_mat"):
            assert (t.get("mo_ta") or "").strip(), t["khoa"]


# ---------------------------------------------------------------------------
# 2. PKCE + URL đăng nhập
# ---------------------------------------------------------------------------

def test_pkce_challenge_la_bam_cua_verifier():
    pkce = mod_oauth.tao_pkce()
    digest = hashlib.sha256(pkce["code_verifier"].encode()).digest()
    mong = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert pkce["code_challenge"] == mong
    assert len(pkce["state"]) >= 32  # state ngẫu nhiên đủ dài
    pkce2 = mod_oauth.tao_pkce()
    assert pkce2["state"] != pkce["state"]
    assert pkce2["code_verifier"] != pkce["code_verifier"]


def test_url_dang_nhap_dung_chuan_pkce():
    cho = mod_oauth.ChoOAuth()
    kq = cho.bat_dau()
    url = kq["auth_url"]
    assert url.startswith("https://auth.openai.com/oauth/authorize?")
    assert "code_challenge_method=S256" in url
    assert f"state={kq['state']}" in url
    assert "client_id=app_EMoamEEZ73f0CkXaXp7hrann" in url
    # redirect_uri PHẢI là loopback 1455 đã khai (khớp tuyệt đối khi đổi mã).
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" in url
    assert "offline_access" in url


# ---------------------------------------------------------------------------
# 3. Đổi mã: state dùng một lần, có hạn, redirect khớp tuyệt đối
# ---------------------------------------------------------------------------

def test_doi_ma_thanh_cong_va_state_dung_mot_lan():
    cho = mod_oauth.ChoOAuth()
    kq = cho.bat_dau()
    http = http_token_ok()
    token = chay(cho.doi_ma("ma-gia-1", kq["state"], http=http))
    assert token["access_token"]
    assert token["refresh_token"] == "refresh-moi"
    # redirect_uri gửi lên máy chủ PHẢI khớp tuyệt đối với giá trị đã đăng ký
    # khi bắt đầu luồng (lưu theo state), không lấy từ tham số ngoài.
    than_gui = http.loi_goi[-1].get("data") or {}
    assert than_gui.get("redirect_uri") == "http://localhost:1455/auth/callback"
    # State đã bị huỷ: dùng lại → lỗi, và KHÔNG gọi mạng lần hai.
    with pytest.raises(mod_oauth.LoiOAuth):
        chay(cho.doi_ma("ma-gia-1", kq["state"], http=http))
    assert http.dem("oauth/token") == 1


def test_lam_moi_khong_log_token_tho(caplog):
    """Làm mới thành công KHÔNG ghi access/refresh token thô vào log."""
    access = "access-bi-mat-khong-log-nv173"
    refresh = "refresh-bi-mat-khong-log-nv173"
    http = http_token_ok(access=access, refresh=refresh)
    with caplog.at_level(logging.INFO, logger="web2api.openai_codex.oauth"):
        kq = chay(mod_oauth.lam_moi_token("refresh-kt-log-nv173", http=http))
    assert kq and kq["access_token"] == access
    tat_ca = "\n".join(r.getMessage() for r in caplog.records)
    assert access not in tat_ca
    assert refresh not in tat_ca


def test_khoa_lam_moi_cung_refresh_tra_cung_khoa():
    """Khoá làm mới theo refresh_token: cùng refresh → CÙNG khoá, refresh
    khác → khoá khác. (Không khoá chung thì hai lượt đồng thời cùng đổi,
    xoay token mất một lượt.)"""
    k1 = mod_oauth._lay_khoa("refresh-a-nv173")
    k2 = mod_oauth._lay_khoa("refresh-a-nv173")
    k3 = mod_oauth._lay_khoa("refresh-b-nv173")
    assert k1 is k2
    assert k1 is not k3


def test_doi_ma_state_la_bi_tu_choi_khong_goi_mang():
    cho = mod_oauth.ChoOAuth()
    cho.bat_dau()
    http = http_token_ok()
    with pytest.raises(mod_oauth.LoiOAuth):
        chay(cho.doi_ma("ma-gia", "state-khong-ton-tai", http=http))
    assert http.dem("oauth/token") == 0


def test_doi_ma_state_het_han():
    cho = mod_oauth.ChoOAuth(han_state_giay=0)  # hết hạn ngay
    kq = cho.bat_dau()
    http = http_token_ok()
    with pytest.raises(mod_oauth.LoiOAuth):
        chay(cho.doi_ma("ma-gia", kq["state"], http=http))
    assert http.dem("oauth/token") == 0


def test_trich_code_tu_url_callback():
    code, state = mod_oauth.trich_code_state(
        "http://localhost:1455/auth/callback?code=abc&state=xyz")
    assert (code, state) == ("abc", "xyz")
    # Host KHÔNG phải loopback → từ chối (chống dán nhầm URL lạ / open redirect).
    with pytest.raises(mod_oauth.LoiOAuth):
        mod_oauth.trich_code_state(
            "https://evil.example.com/auth/callback?code=abc&state=xyz")
    # Host lạ nhưng ĐÚNG scheme/cổng/đường dẫn → vẫn từ chối (đây là ca kiểm
    # tra riêng của kiểm tra host, không phụ thuộc vào scheme/cổng).
    with pytest.raises(mod_oauth.LoiOAuth):
        mod_oauth.trich_code_state(
            "http://evil.example.com:1455/auth/callback?code=abc&state=xyz")
    # Cổng khác 1455 → từ chối (redirect_uri phải khớp tuyệt đối).
    with pytest.raises(mod_oauth.LoiOAuth):
        mod_oauth.trich_code_state(
            "http://localhost:9999/auth/callback?code=abc&state=xyz")
    # Thiếu code → từ chối.
    with pytest.raises(mod_oauth.LoiOAuth):
        mod_oauth.trich_code_state("http://localhost:1455/auth/callback?state=xyz")


# ---------------------------------------------------------------------------
# 4. Làm mới token: phân loại lỗi theo cấu trúc, khoá chống đua, lùi dần
# ---------------------------------------------------------------------------

def test_lam_moi_thanh_cong_xoay_token():
    http = http_token_ok(access=tao_jwt(email="b@example.com"),
                         refresh="refresh-v2")
    kq = chay(mod_oauth.lam_moi_token("refresh-v1", http=http))
    assert kq["refresh_token"] == "refresh-v2"
    assert kq["expires_at"] > time.time()


def test_lam_moi_loi_khong_khoi_phuc_duoc():
    def xu_ly(method, url, **kw):
        return PhanHoiGia(400, json_={"error": "refresh_token_reused"})
    kq = chay(mod_oauth.lam_moi_token("refresh-chet", http=HttpGia(xu_ly)))
    assert kq == {"error": "unrecoverable", "code": "refresh_token_reused"} or (
        kq["error"] == "unrecoverable" and kq["code"] == "refresh_token_reused")


def test_lam_moi_loi_tam_thoi_tra_none_khong_danh_dau():
    def xu_ly_500(method, url, **kw):
        return PhanHoiGia(503, json_={"error": "temporarily_unavailable"})
    kq = chay(mod_oauth.lam_moi_token("refresh-tam", http=HttpGia(xu_ly_500)))
    assert kq is None

    def xu_ly_mang(method, url, **kw):
        raise ConnectionError("mạng đứt")
    kq2 = chay(mod_oauth.lam_moi_token("refresh-tam-2", http=HttpGia(xu_ly_mang)))
    assert kq2 is None


def test_lam_moi_dong_thoi_chi_mot_loi_goi_mang():
    """Hai lượt làm mới CÙNG refresh_token đồng thời → đúng MỘT POST (khoá +
    kiểm lại kết quả vừa làm mới), tránh đua xoay token làm mất token mới."""
    http = http_token_ok(refresh="refresh-chung")

    async def chay_doi():
        return await asyncio.gather(
            mod_oauth.lam_moi_token("refresh-dong-thoi", http=http),
            mod_oauth.lam_moi_token("refresh-dong-thoi", http=http),
        )
    kq1, kq2 = chay(chay_doi())
    assert kq1["access_token"] == kq2["access_token"]
    assert http.dem("oauth/token") == 1


def test_lui_dan_tinh_dung():
    # Chuỗi lùi: 1, 2, 4, ... trần 60; không âm.
    ds = [mod_oauth.lui_dan_giay(n) for n in range(0, 8)]
    assert ds[0] >= 0 and all(b >= a for a, b in zip(ds, ds[1:]))
    assert ds[-1] <= 60


# ---------------------------------------------------------------------------
# 5. Bộ nghe callback: chỉ loopback, đóng sau khi xong
# ---------------------------------------------------------------------------

def test_bo_nghe_callback_loopback_va_dong_sau_xong():
    import http.client
    cho = mod_oauth.ChoOAuth()
    kq = cho.bat_dau()
    nghe = mod_oauth.BoNgheCallback(cho, cong=0)  # cổng 0 = hệ điều hành cấp
    nghe.bat_dau()
    try:
        assert nghe.cong_thuc > 0
        # Máy chủ chỉ nghe trên 127.0.0.1 — kiểm tại SOCKET thật, không chỉ
        # thuộc tính khai báo.
        assert nghe.dia_chi_nghe == "127.0.0.1"
        assert nghe._may_chu.server_address[0] == "127.0.0.1"
        conn = http.client.HTTPConnection("127.0.0.1", nghe.cong_thuc, timeout=5)
        conn.request("GET", f"/auth/callback?code=ma-x&state={kq['state']}")
        phan_hoi = conn.getresponse()
        than = phan_hoi.read()
        assert phan_hoi.status in (200, 400, 502)  # đã nhận callback
        assert b"ma-x" not in than  # mã KHÔNG phản chiếu vào trang trả về
        ket_qua = nghe.lay_ket_qua(cho_giay=5)
        assert ket_qua is not None
    finally:
        nghe.dung()
    # Sau khi đóng: kết nối bị từ chối.
    with pytest.raises(OSError):
        conn2 = http.client.HTTPConnection("127.0.0.1", nghe.cong_thuc, timeout=2)
        conn2.request("GET", "/auth/callback")
        conn2.getresponse()


# ---------------------------------------------------------------------------
# 6. Hạn mức: header + wham, khoá băm đầy đủ
# ---------------------------------------------------------------------------

HEADER_CODEX = {
    "x-codex-plan-type": "plus",
    "x-codex-active-limit": "premium",
    "x-codex-primary-used-percent": "42",
    "x-codex-primary-window-minutes": "43200",
    "x-codex-primary-reset-at": "1790000000",
    "x-codex-secondary-used-percent": "10",
    "x-codex-secondary-window-minutes": "300",
    "x-codex-credits-has-credits": "False",
    "x-codex-credits-unlimited": "False",
}


def test_doc_header_han_muc():
    ban = mod_hanmuc.doc_header(dict(HEADER_CODEX))
    assert ban is not None
    assert ban["plan"] == "plus"
    assert ban["chinh"]["da_dung_pct"] == 42.0
    assert ban["chinh"]["con_lai_pct"] == 58.0
    assert ban["chinh"]["nhan"] == "30 ngày"
    assert ban["phu"]["da_dung_pct"] == 10.0
    assert ban["cham_tran"] is False
    assert mod_hanmuc.doc_header({}) is None
    assert mod_hanmuc.doc_header({"x-other": "1"}) is None


def test_doc_wham_han_muc():
    payload = {
        "email": "nguoi.a@example.com",
        "plan_type": "plus",
        "rate_limit": {
            "limit_reached": True,
            "primary_window": {"used_percent": 100, "limit_window_seconds": 2592000,
                               "reset_at": 1790000000, "reset_after_seconds": 3600},
            "secondary_window": {"used_percent": 3, "limit_window_seconds": 18000,
                                 "reset_at": 1790001000, "reset_after_seconds": 60},
        },
        "credits": {"has_credits": True, "unlimited": False},
        "rate_limit_reset_credits": {"available_count": 2},
    }
    ban = mod_hanmuc.doc_wham(payload)
    assert ban["email"] == "nguoi.a@example.com"
    assert ban["chinh"]["da_dung_pct"] == 100.0
    assert ban["chinh"]["cua_so_phut"] == 43200  # giây → phút, cùng hình header
    assert ban["cham_tran"] is True
    assert ban["credit_reset"] == 2


def test_kho_han_muc_khoa_bang_bam_day_du():
    """Hai token KHÁC nhau nhưng trùng 40 ký tự đầu (header JWT giống nhau)
    phải là HAI bản ghi — bản lỗi ở c2a dùng token[:40] làm khoá nên gộp."""
    kho = mod_hanmuc.KhoHanMuc()
    # Hai token khác nhau nhưng TRÙNG 40 ký tự đầu (đúng tình huống ở c2a:
    # phần header JWT base64 giống hệt giữa các tài khoản cùng nguồn phát).
    tien_to = tao_jwt()[:40]
    t1 = tien_to + ".payload-mot.chu-ky-mot"
    t2 = tien_to + ".payload-hai.chu-ky-hai"
    assert t1 != t2 and t1[:40] == t2[:40]  # tiền đề của lỗi cũ
    kho.ghi(t1, mod_hanmuc.doc_header({**HEADER_CODEX,
                                       "x-codex-primary-used-percent": "11"}))
    kho.ghi(t2, mod_hanmuc.doc_header({**HEADER_CODEX,
                                       "x-codex-primary-used-percent": "99"}))
    assert kho.lay(t1)["chinh"]["da_dung_pct"] == 11.0
    assert kho.lay(t2)["chinh"]["da_dung_pct"] == 99.0


def test_thong_tin_token_tu_claim():
    tt = mod_hanmuc.thong_tin_token(tao_jwt(email="x@example.com", plan="team"))
    assert tt["la_jwt"] is True
    assert tt["email"] == "x@example.com"
    assert tt["plan"] == "team"
    assert tt["account_id"] == "acc-123"
    tt2 = mod_hanmuc.thong_tin_token("khong-phai-jwt")
    assert tt2["la_jwt"] is False


# ---------------------------------------------------------------------------
# 7. Module: vòng đời, thực thể thiết bị, hành động OAuth
# ---------------------------------------------------------------------------

def _gan_token_ok(module, access=None, refresh="refresh-moi", dem=None):
    module._gan_transport_gia(_transport_token_ok(access, refresh, dem))
    return module


def test_module_khong_tai_khoan_suc_khoe(module):
    sk = chay(module.suc_khoe())
    assert sk["ok"] is True
    assert sk["so_tai_khoan"] == 0
    tt = chay(module.thuc_the())
    ids = [e["id"] for e in tt]
    assert "openaicodex.dang-nhap" in ids  # thực thể bắt đầu đăng nhập luôn có


def test_hanh_dong_bat_dau_dang_nhap_tra_url(module):
    kq = chay(module.thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap", {}))
    assert kq["ok"] is True
    url = kq["trang_thai_moi"]["auth_url"]
    assert url.startswith("https://auth.openai.com/oauth/authorize?")
    # URL đăng nhập KHÔNG chứa bí mật nào của tài khoản.
    assert "refresh" not in url


def test_dan_callback_them_tai_khoan_va_khong_lo_token(module, caplog):
    _gan_token_ok(module)
    kq1 = chay(module.thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap", {}))
    state = kq1["trang_thai_moi"]["state"]
    url_cb = f"http://localhost:1455/auth/callback?code=ma-1&state={state}"
    with caplog.at_level("DEBUG"):
        kq2 = chay(module.thuc_hien("openaicodex.dang-nhap", "dan_callback",
                                    {"url_callback": url_cb}))
    assert kq2["ok"] is True
    # Tài khoản xuất hiện dưới dạng thực thể, email hiển thị được.
    tt = chay(module.thuc_the())
    tk_ids = [e["id"] for e in tt if e["id"].startswith("openaicodex.tk-")]
    assert len(tk_ids) == 1
    # Token KHÔNG xuất hiện trong bất kỳ đầu ra nào.
    chuoi_ra = json.dumps(kq2, ensure_ascii=False) + json.dumps(tt, ensure_ascii=False)
    ban_ghi = module.kho.doc("tai_khoan")
    token_that = ban_ghi["tai_khoan"][0]["access_token"]
    assert token_that and token_that not in chuoi_ra
    assert token_that not in caplog.text
    assert "ma-1" not in caplog.text  # authorization code cũng không vào log


def test_dan_callback_state_sai_bi_tu_choi(module):
    _gan_token_ok(module)
    chay(module.thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap", {}))
    kq = chay(module.thuc_hien(
        "openaicodex.dang-nhap", "dan_callback",
        {"url_callback": "http://localhost:1455/auth/callback?code=x&state=sai"}))
    assert kq["ok"] is False
    assert chay(module.suc_khoe())["so_tai_khoan"] == 0


def test_dan_callback_url_host_la_bi_tu_choi(module):
    _gan_token_ok(module)
    chay(module.thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap", {}))
    kq = chay(module.thuc_hien(
        "openaicodex.dang-nhap", "dan_callback",
        {"url_callback": "https://evil.example.com/auth/callback?code=x&state=y"}))
    assert kq["ok"] is False
    # Host lạ nhưng đúng scheme http + cổng 1455 + đúng đường dẫn: vẫn từ chối.
    kq2 = chay(module.thuc_hien(
        "openaicodex.dang-nhap", "dan_callback",
        {"url_callback": "http://evil.example.com:1455/auth/callback?code=x&state=y"}))
    assert kq2["ok"] is False
    # Không tài khoản nào được thêm.
    assert chay(module.suc_khoe())["so_tai_khoan"] == 0


def _them_tai_khoan(module, email="nguoi.a@example.com"):
    """Thêm một tài khoản qua đường dán callback (đường chính thức)."""
    http = _gan_token_ok(module, access=tao_jwt(email=email))
    kq1 = chay(module.thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap", {}))
    state = kq1["trang_thai_moi"]["state"]
    kq2 = chay(module.thuc_hien(
        "openaicodex.dang-nhap", "dan_callback",
        {"url_callback": f"http://localhost:1455/auth/callback?code=m&state={state}"}))
    assert kq2["ok"] is True
    return kq2["trang_thai_moi"]["tai_khoan_id"]


def test_hanh_dong_lam_moi_va_xoa_tai_khoan(module):
    tk_id = _them_tai_khoan(module)
    _gan_token_ok(module)  # làm mới cũng qua máy chủ giả
    kq = chay(module.thuc_hien(tk_id, "lam_moi", {}))
    assert kq["ok"] is True
    # Làm mới ĐỔI token → khoá tài khoản (suy từ token) đổi theo: lấy lại
    # id thực thể hiện tại trước khi thao tác tiếp.
    tk_id = next(e["id"] for e in chay(module.thuc_the())
                 if e["id"].startswith("openaicodex.tk-"))
    # Xoá phải có xac_nhan="xoa".
    kq_sai = chay(module.thuc_hien(tk_id, "xoa_tai_khoan", {}))
    assert kq_sai["ok"] is False
    kq_xoa = chay(module.thuc_hien(tk_id, "xoa_tai_khoan", {"xac_nhan": "xoa"}))
    assert kq_xoa["ok"] is True
    assert chay(module.suc_khoe())["so_tai_khoan"] == 0
    # IDOR: id thực thể lạ (không thuộc module) → không tìm thấy, không lỗi sập.
    kq_la = chay(module.thuc_hien("openaicodex.tk-khong-co", "lam_moi", {}))
    assert kq_la["ok"] is False


def test_luu_token_0600_va_khoi_dong_lai_con_tai_khoan(module, tmp_path):
    _them_tai_khoan(module)
    tep = tmp_path / "bi_mat" / "mo-dun" / "openaicodex" / "tai_khoan.json"
    assert tep.is_file()
    assert (tep.stat().st_mode & 0o777) == 0o600
    # Module mới (khởi động lại) nạp lại được tài khoản từ kho.
    from app import kho
    m2 = Module(dict(MANIFEST))
    m2.kho = kho.kho_rieng("openaicodex")
    m2.nap({})
    assert chay(m2.suc_khoe())["so_tai_khoan"] == 1


# ---------------------------------------------------------------------------
# 8. lam_moi_phien (hợp đồng lõi — vòng tự làm mới định kỳ)
# ---------------------------------------------------------------------------

def test_lam_moi_phien_khi_sap_het_han(module):
    from app.khoi_phuc_phien import KetQuaKhoiPhuc
    tk_id = _them_tai_khoan(module)
    # Ép token sắp hết hạn (còn 1 giờ) → phải làm mới thật.
    ban_ghi = module.kho.doc("tai_khoan")
    ban_ghi["tai_khoan"][0]["expires_at"] = time.time() + 3600
    module.kho.luu("tai_khoan", ban_ghi)
    module.nap({})
    _gan_token_ok(module)
    khoa = module.khoa_tai_khoan()[0]
    kq = chay(module.lam_moi_phien(khoa))
    assert isinstance(kq, KetQuaKhoiPhuc)
    assert kq.thanh_cong is True


def test_lam_moi_phien_con_han_khong_goi_mang(module):
    tk_id = _them_tai_khoan(module)
    # token vừa đổi có hạn 8h; ngưỡng c2a là còn ≤6h mới làm mới → chưa cần.
    dem: list[str] = []
    _gan_token_ok(module, dem=dem)
    khoa = module.khoa_tai_khoan()[0]
    kq = chay(module.lam_moi_phien(khoa))
    assert kq.thanh_cong is True
    assert dem == []


def test_lam_moi_phien_refresh_chet_danh_dau_vo_hieu(module):
    tk_id = _them_tai_khoan(module)
    ban_ghi = module.kho.doc("tai_khoan")
    ban_ghi["tai_khoan"][0]["expires_at"] = time.time() - 10  # đã hết hạn
    module.kho.luu("tai_khoan", ban_ghi)
    module.nap({})

    def xu_ly(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})
    module._gan_transport_gia(httpx.MockTransport(xu_ly))
    khoa = module.khoa_tai_khoan()[0]
    kq = chay(module.lam_moi_phien(khoa))
    assert kq.thanh_cong is False
    tt = chay(module.trang_thai([tk_id]))
    assert tt[tk_id]["gia_tri"] in ("vo_hieu", "deactivated")


def test_lam_moi_phien_loi_mang_tam_khong_danh_dau_hong(module):
    tk_id = _them_tai_khoan(module)
    ban_ghi = module.kho.doc("tai_khoan")
    ban_ghi["tai_khoan"][0]["expires_at"] = time.time() - 10
    module.kho.luu("tai_khoan", ban_ghi)
    module.nap({})

    def xu_ly(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("đứt mạng tạm", request=req)
    module._gan_transport_gia(httpx.MockTransport(xu_ly))
    khoa = module.khoa_tai_khoan()[0]
    kq = chay(module.lam_moi_phien(khoa))
    assert kq.thanh_cong is False
    tt = chay(module.trang_thai([tk_id]))
    # Lỗi mạng tạm KHÔNG được biến tài khoản thành hỏng.
    assert tt[tk_id]["gia_tri"] == "hoat_dong"


# ---------------------------------------------------------------------------
# 9. Deactivated: đánh dấu + chờ duyệt xoá/giữ
# ---------------------------------------------------------------------------

def test_deactivated_cho_duyet_va_giai_quyet(module):
    tk_id = _them_tai_khoan(module, email="chet@example.com")
    module.danh_dau_deactivated(tk_id, "account_deactivated")
    tt = chay(module.trang_thai([tk_id]))
    assert tt[tk_id]["gia_tri"] == "deactivated"
    cho = module.danh_sach_cho_duyet()
    assert len(cho) == 1 and cho[0]["email"] == "chet@example.com"
    # Giữ: bỏ chờ duyệt, trạng thái vẫn deactivated (không tự sống lại).
    kq = chay(module.thuc_hien(tk_id, "giu_tai_khoan", {}))
    assert kq["ok"] is True
    assert module.danh_sach_cho_duyet() == []
    assert chay(module.trang_thai([tk_id]))[tk_id]["gia_tri"] == "deactivated"
    # Xoá: hết tài khoản + hết chờ duyệt.
    module.danh_dau_deactivated(tk_id, "account_deactivated")
    kq2 = chay(module.thuc_hien(tk_id, "xoa_tai_khoan", {"xac_nhan": "xoa"}))
    assert kq2["ok"] is True
    assert module.danh_sach_cho_duyet() == []


# ---------------------------------------------------------------------------
# 10. Chat: xoay tài khoản, 401 → làm mới → thử lại, 429 → giới hạn + hồi phục
# ---------------------------------------------------------------------------

SSE_CO_TEX = (
    'data: {"type": "response.output_text.delta", "delta": "Xin "}\n\n'
    'data: {"type": "response.output_text.delta", "delta": "chào"}\n\n'
    'data: {"type": "response.completed", "response": {}}\n\n'
)


def _transport_chat(che_do_theo_token, dem_token=None):
    """Máy chủ Codex giả (MockTransport): hành vi theo access token."""
    def xu_ly(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "oauth/token" in url:
            if dem_token is not None:
                dem_token.append(url)
            return httpx.Response(200, json={
                "access_token": tao_jwt(email="moi@example.com"),
                "refresh_token": "refresh-sau-401", "expires_in": 28800})
        if "codex/responses" in url:
            token = req.headers.get("authorization", "")[7:]
            hv = che_do_theo_token.get(token, "ok")
            if hv == "401":
                return httpx.Response(401, json={"error": {"code": "invalid_token"}})
            if hv == "429":
                return httpx.Response(429, json={"error": {
                    "code": "usage_limit_reached", "resets_at": 1790000000}},
                    headers={"x-codex-primary-used-percent": "100",
                             "x-codex-primary-window-minutes": "43200",
                             "x-codex-primary-reset-at": "1790000000"})
            if hv == "403":
                return httpx.Response(403, json={"error": {"code": "forbidden"}})
            return httpx.Response(200, content=SSE_CO_TEX.encode(),
                                  headers={**HEADER_CODEX,
                                           "content-type": "text/event-stream"})
        if "wham/usage" in url:
            return httpx.Response(200, json={"plan_type": "plus", "rate_limit": {}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(xu_ly)


def _nap_tai_khoan_thang(module, access, refresh="refresh-goc",
                         email="nguoi.a@example.com"):
    """Nạp thẳng một tài khoản vào kho (bỏ qua luồng OAuth) cho test chat."""
    module.kho.luu("tai_khoan", {"tai_khoan": [{
        "access_token": access, "refresh_token": refresh,
        "email": email, "expires_at": time.time() + 8 * 3600,
        "trang_thai": "hoat_dong"}]})
    module.nap({})
    tt = chay(module.thuc_the())
    return [e["id"] for e in tt if e["id"].startswith("openaicodex.tk-")][0]


def test_chat_khong_tai_khoan_tra_loi_ro(module):
    ma, _headers, than = chay(module.chat(
        {"model": "openaicodex/gpt-5.5",
         "messages": [{"role": "user", "content": "chào"}]}, False))
    assert ma in (401, 503)
    assert b"refresh" not in than  # lỗi không lộ bí mật


def test_chat_non_stream_va_ghi_han_muc_tu_header(module):
    token = tao_jwt()
    _nap_tai_khoan_thang(module, token)
    module._gan_transport_gia(_transport_chat({token: "ok"}))
    ma, _headers, than = chay(module.chat(
        {"model": "openaicodex/gpt-5.5",
         "messages": [{"role": "user", "content": "chào"}]}, False))
    assert ma == 200
    noi_dung = json.loads(than)
    assert noi_dung["choices"][0]["message"]["content"] == "Xin chào"
    # Header x-codex-* đã được ghi vào kho hạn mức.
    ban = mod_hanmuc.kho_han_muc.lay(token)
    assert ban is not None and ban["chinh"]["da_dung_pct"] == 42.0


def test_chat_stream_tra_sse(module):
    token = tao_jwt()
    _nap_tai_khoan_thang(module, token)
    module._gan_transport_gia(_transport_chat({token: "ok"}))

    async def doc_luong():
        ma, _h, luong = await module.chat(
            {"model": "openaicodex/gpt-5.5",
             "messages": [{"role": "user", "content": "chào"}]}, True)
        assert ma == 200
        cac_khuc = []
        async for khuc in luong:
            cac_khuc.append(khuc if isinstance(khuc, bytes) else khuc.encode())
        return b"".join(cac_khuc)
    than = chay(doc_luong())
    assert b"Xin " in than and b"chat.completion.chunk" in than


def test_chat_401_lam_moi_roi_thu_lai(module):
    token_cu = tao_jwt(email="cu@example.com")
    _nap_tai_khoan_thang(module, token_cu)
    dem_token: list[str] = []
    # token mới (sau làm mới) không có trong bảng chế độ → "ok"
    module._gan_transport_gia(_transport_chat({token_cu: "401"}, dem_token))
    ma, _h, than = chay(module.chat(
        {"model": "openaicodex/gpt-5.5",
         "messages": [{"role": "user", "content": "chào"}]}, False))
    assert ma == 200
    assert json.loads(than)["choices"][0]["message"]["content"] == "Xin chào"
    assert len(dem_token) == 1  # đúng một lần làm mới


def test_chat_429_danh_dau_gioi_han_va_co_thoi_diem_hoi(module):
    token = tao_jwt()
    tk_id = _nap_tai_khoan_thang(module, token)
    module._gan_transport_gia(_transport_chat({token: "429"}))
    ma, _h, _than = chay(module.chat(
        {"model": "openaicodex/gpt-5.5",
         "messages": [{"role": "user", "content": "chào"}]}, False))
    assert ma == 429
    tt = chay(module.trang_thai([tk_id]))
    assert tt[tk_id]["gia_tri"] == "gioi_han"


def test_chat_403_vo_hieu_tai_khoan(module):
    token = tao_jwt()
    tk_id = _nap_tai_khoan_thang(module, token)
    module._gan_transport_gia(_transport_chat({token: "403"}))
    chay(module.chat({"model": "openaicodex/gpt-5.5",
                      "messages": [{"role": "user", "content": "x"}]}, False))
    tt = chay(module.trang_thai([tk_id]))
    assert tt[tk_id]["gia_tri"] == "vo_hieu"


def test_chat_model_khong_ho_tro_bi_400(module):
    """Model ngoài danh sách khai báo → 400 model_not_found, KHÔNG gọi mạng."""
    token = tao_jwt()
    _nap_tai_khoan_thang(module, token)
    dem: list[str] = []
    module._gan_transport_gia(_transport_chat({token: "ok"}, dem))
    ma, _h, than = chay(module.chat(
        {"model": "openaicodex/model-khong-ton-tai",
         "messages": [{"role": "user", "content": "x"}]}, False))
    assert ma == 400
    assert "model-khong-ton-tai" in than.decode("utf-8", "replace")
    assert dem == []  # không gọi Codex lần nào


def test_chat_401_ba_lan_lien_tiep_khong_lam_moi_duoc_vo_hieu(module):
    """Ngưỡng 401: chat 401 + refresh đã chết (invalid_grant) — 3 lượt liên
    tiếp → tài khoản bị vô hiệu (ngừng phục vụ), không thử vô hạn."""
    token = tao_jwt()
    tk_id = _nap_tai_khoan_thang(module, token)

    def xu_ly(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "oauth/token" in url:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if "codex/responses" in url:
            return httpx.Response(
                401, json={"error": {"code": "invalid_token"}})
        return httpx.Response(404, json={})
    module._gan_transport_gia(httpx.MockTransport(xu_ly))

    for _ in range(3):
        ma, _h, _than = chay(module.chat(
            {"model": "openaicodex/gpt-5.5",
             "messages": [{"role": "user", "content": "x"}]}, False))
        assert ma == 401
    tt = chay(module.trang_thai([tk_id]))
    assert tt[tk_id]["gia_tri"] == "vo_hieu"


def test_phan_loai_loi_theo_cau_truc(module):
    assert module.phan_loai_loi(429, {}, {}) == "het_han_muc"
    assert module.phan_loai_loi(401, {}, {}) == "khoa_hong"
    assert module.phan_loai_loi(403, {}, {}) == "khoa_hong"
    assert module.phan_loai_loi(502, {}, {}) == "may_chu_loi"
    assert module.phan_loai_loi(400, {}, {}) in (None, "yeu_cau_sai")


# ---------------------------------------------------------------------------
# 11. Cấp API (qua lõi thật): 401/403/IDOR trên các đường module dùng
# ---------------------------------------------------------------------------

KHOA_QT_API = "khoa-quan-tri-test-nv173-0123456789ab"
QT_API = {"Authorization": f"Bearer {KHOA_QT_API}"}


@pytest.fixture
def app_co_module(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT_API)
    dich = tmp_path / "modules" / "openaicodex"
    shutil.copytree(THU_MUC, dich)
    from app import so_module
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.post("/api/modules/nap-lai", headers=QT_API)
        assert r.status_code == 200, r.text
        r2 = c.post("/api/modules/openaicodex/cai", headers=QT_API,
                    json={"cau_hinh": {}})
        assert r2.status_code == 200 and r2.json()["ok"] is True, r2.text
        yield c


def test_api_thiet_bi_can_xac_thuc(app_co_module):
    c = app_co_module
    # Không khoá → 401.
    assert c.get("/api/thiet-bi").status_code == 401
    assert c.post("/api/thiet-bi/openaicodex.dang-nhap/hanh-dong",
                  json={"hanh_dong": "bat_dau_dang_nhap"}).status_code == 401
    # Quản trị → thấy thực thể đăng nhập, KHÔNG thấy token nào.
    r = c.get("/api/thiet-bi", headers=QT_API)
    assert r.status_code == 200
    than = r.text
    ids = [e["id"] for e in r.json()["thuc_the"]]
    assert "openaicodex.dang-nhap" in ids
    assert "refresh" not in than.lower().replace("refresh token", "")


def test_api_hanh_dong_va_idor(app_co_module):
    c = app_co_module
    # Quản trị bắt đầu đăng nhập → nhận auth_url.
    r = c.post("/api/thiet-bi/openaicodex.dang-nhap/hanh-dong",
               headers=QT_API,
               json={"hanh_dong": "bat_dau_dang_nhap", "tham_so": {},
                     "xac_nhan": True})
    assert r.status_code == 200, r.text
    assert "auth.openai.com/oauth/authorize" in r.text
    # IDOR: thực thể không tồn tại / thuộc module khác → 404.
    r404 = c.post("/api/thiet-bi/openaicodex.tk-khong-ton-tai/hanh-dong",
                  headers=QT_API,
                  json={"hanh_dong": "lam_moi", "tham_so": {},
                        "xac_nhan": True})
    assert r404.status_code == 404
    r404b = c.post("/api/thiet-bi/module-khac.tk-abc/hanh-dong",
                   headers=QT_API,
                   json={"hanh_dong": "lam_moi", "tham_so": {},
                         "xac_nhan": True})
    assert r404b.status_code == 404


def test_api_khoa_khach_khong_co_pham_vi_thiet_bi_bi_403(app_co_module):
    c = app_co_module
    # Tạo khoá khách chỉ có phạm vi chat.
    r = c.post("/api/khoa-khach", headers=QT_API,
               json={"ten": "khach-nv173", "pham_vi": ["chat"]})
    assert r.status_code in (200, 201), r.text
    khoa_khach = (r.json().get("khoa") or {}).get("khoa")
    assert isinstance(khoa_khach, str) and khoa_khach
    khach = {"Authorization": f"Bearer {khoa_khach}"}
    # Khoá khách gọi đường thiết bị → 403 (không đủ phạm vi thiet_bi:ghi).
    r403 = c.post("/api/thiet-bi/openaicodex.dang-nhap/hanh-dong",
                  headers=khach,
                  json={"hanh_dong": "bat_dau_dang_nhap", "tham_so": {},
                        "xac_nhan": True})
    assert r403.status_code == 403
    # /v1/chat/completions không khoá → 401.
    assert c.post("/v1/chat/completions",
                  json={"model": "openaicodex/gpt-5.5",
                        "messages": [{"role": "user", "content": "x"}]}
                  ).status_code == 401
