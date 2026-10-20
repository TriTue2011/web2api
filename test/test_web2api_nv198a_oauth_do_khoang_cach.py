"""Đo khoảng cách OAuth + làm mới token c2a ↔ web2api (NV198a).

CHỈ ĐO — không sửa app/modules. Mỗi luồng = MỘT test chạy qua LÕI THẬT
(kham_pha.kiem_manifest → nap_ma → ModuleKhamPha.tao → kho.kho_rieng)
với MÁY CHỦ OAUTH GIẢ chạy cục bộ (HTTP thật trên 127.0.0.1, cổng ngẫu nhiên).

Máy chủ giả đóng cả hai vai:
- POST /oauth/token : máy chủ OAuth (đổi code, làm mới refresh_token)
- POST /backend-api/codex/responses : API Codex (trả 401/200 theo kịch bản)

Không gọi mạng thật: conftest chặn mọi thứ ngoài 127.0.0.1; hằng số
CODEX_TOKEN_URL của oauth.py được trỏ về máy chủ giả (mã module chạy thật,
chỉ đổi đích mạng — đúng tinh thần «máy chủ OAuth GIẢ chạy cục bộ»).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import kham_pha, kho

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "openaicodex"
ID_MODULE = "openaicodex"


def chay(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# JWT giả + máy chủ OAuth giả
# ---------------------------------------------------------------------------

def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(d).encode()).rstrip(b"=").decode()


_DEM_JWT = 0


def tao_jwt(email="nguoi.a@example.com", plan="plus", het_sau_giay=8 * 3600,
            account_id="acc-123") -> str:
    """JWT giả đúng cấu trúc claim Codex (chưa ký — module chỉ đọc claim).

    Mỗi lần gọi khác chuỗi (jti tăng dần) để test phân biệt được token cũ/mới
    ngay cả khi hai lần cấp cùng một giây."""
    global _DEM_JWT
    _DEM_JWT += 1
    payload = {
        "email": email,
        "exp": int(time.time()) + het_sau_giay,
        "jti": f"gia-{_DEM_JWT}",
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
        },
        "https://api.openai.com/profile": {"email": email},
    }
    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.chu-ky-gia"


class MayChuOAuthGia:
    """Máy chủ OAuth + Codex giả. `kich_ban` điều khiển hành vi làm mới."""

    def __init__(self):
        self.kich_ban = "thanh_cong"  # thanh_cong | invalid_grant | loi_tam
        self.so_lan_doi_code = 0
        self.so_lan_lam_moi = 0
        self.so_lan_codex = 0
        self.than_lam_moi_cuoi: dict = {}
        self.than_doi_code_cuoi: dict = {}
        self.token_codex_hop_le: str | None = None  # Bearer nào thì Codex 200
        self._khoa = threading.Lock()
        self._may_chu: ThreadingHTTPServer | None = None
        self._luong: threading.Thread | None = None
        self.url_goc = ""

    # -- HTTP -----------------------------------------------------------
    def bat_dau(self):
        ngoai = self

        class _XuLy(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: ARG002
                pass

            def _doc_than(self) -> dict:
                dai = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(dai).decode("utf-8", "ignore") if dai else ""
                return {k: v[0] for k, v in
                        urllib.parse.parse_qs(raw).items()}

            def _tra_json(self, ma: int, obj: dict):
                than = json.dumps(obj).encode()
                self.send_response(ma)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(than)))
                self.end_headers()
                self.wfile.write(than)

            def do_POST(self):  # noqa: N802
                if self.path == "/oauth/token":
                    than = self._doc_than()
                    loai = than.get("grant_type")
                    if loai == "authorization_code":
                        with ngoai._khoa:
                            ngoai.so_lan_doi_code += 1
                            ngoai.than_doi_code_cuoi = dict(than)
                        self._tra_json(200, {
                            "access_token": tao_jwt(),
                            "refresh_token": "RT-TU-CODE-1",
                            "expires_in": 28800,
                            "token_type": "Bearer",
                        })
                        return
                    if loai == "refresh_token":
                        with ngoai._khoa:
                            ngoai.so_lan_lam_moi += 1
                            ngoai.than_lam_moi_cuoi = dict(than)
                            n = ngoai.so_lan_lam_moi
                            kich_ban = ngoai.kich_ban
                        if kich_ban == "invalid_grant":
                            self._tra_json(400, {"error": "invalid_grant"})
                            return
                        if kich_ban == "loi_tam":
                            self._tra_json(500, {"error": "server_error"})
                            return
                        self._tra_json(200, {
                            "access_token": tao_jwt(
                                het_sau_giay=8 * 3600),
                            "refresh_token": f"RT-MOI-{n}",
                            "expires_in": 28800,
                            "token_type": "Bearer",
                        })
                        return
                    self._tra_json(400, {"error": "unsupported_grant_type"})
                    return
                if self.path == "/backend-api/codex/responses":
                    with ngoai._khoa:
                        ngoai.so_lan_codex += 1
                    auth = self.headers.get("Authorization") or ""
                    token = auth[7:] if auth.startswith("Bearer ") else ""
                    with ngoai._khoa:
                        hop_le = (ngoai.token_codex_hop_le is not None
                                  and token == ngoai.token_codex_hop_le)
                    if not hop_le:
                        self._tra_json(401, {"error": {
                            "message": "unauthorized", "type": "auth_error"}})
                        return
                    sse = ('data: {"type":"response.completed"}\n\n'
                           'data: [DONE]\n\n').encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(sse)))
                    self.end_headers()
                    self.wfile.write(sse)
                    return
                self._tra_json(404, {"error": "not_found"})

        self._may_chu = ThreadingHTTPServer(("127.0.0.1", 0), _XuLy)
        cong = self._may_chu.server_address[1]
        self.url_goc = f"http://127.0.0.1:{cong}"
        self._luong = threading.Thread(target=self._may_chu.serve_forever,
                                       kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self._luong.start()

    def dung(self):
        if self._may_chu is not None:
            self._may_chu.shutdown()
            self._may_chu.server_close()
            self._may_chu = None


# ---------------------------------------------------------------------------
# Nạp module qua LÕI THẬT
# ---------------------------------------------------------------------------

@pytest.fixture()
def may_chu(monkeypatch, tmp_path):
    """Máy chủ giả + WEB2API_DATA trỏ vào thư mục tạm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mc = MayChuOAuthGia()
    mc.bat_dau()
    yield mc
    mc.dung()


_DEM_NAP = 0


def nap_module_qua_loi(monkeypatch):
    """Nạp openaicodex đúng đường lõi: kiem_manifest → nap_ma →
    ModuleKhamPha.tao → gán kho.kho_rieng (như so_module).

    Mỗi lần nạp dùng vân tay RIÊNG để tên gói duy nhất — tránh rò rỉ
    global (cache làm mới, khoá) giữa các test qua sys.modules."""
    global _DEM_NAP
    _DEM_NAP += 1
    vt = f"nv198a-do-{_DEM_NAP}"
    manifest = json.loads((THU_MUC / "manifest.json").read_text(
        encoding="utf-8"))
    assert kham_pha.kiem_manifest(manifest) is None, "manifest hỏng"
    lop, ten = kham_pha.nap_ma(THU_MUC, manifest, vt)
    km = kham_pha.ModuleKhamPha(
        id=manifest["id"], nguon="du_lieu", thu_muc=THU_MUC,
        manifest=manifest, lop=lop, van_tay=vt, ten_ma=ten)
    assert km.dung_chuan, f"không đúng chuẩn: {km.chuan_loi}"
    the = km.tao()
    the.kho = kho.kho_rieng(manifest["id"])
    # Trỏ máy chủ OAuth về máy chủ giả (mã module chạy thật, chỉ đổi đích).
    import sys
    goi = sys.modules[ten]
    return the, goi


@pytest.fixture()
def mod(may_chu, monkeypatch):
    the, goi = nap_module_qua_loi(monkeypatch)
    monkeypatch.setattr(goi.oauth, "CODEX_TOKEN_URL",
                        may_chu.url_goc + "/oauth/token", raising=False)
    goi.oauth.don_bo_dem_lam_moi()
    yield the, goi
    try:
        chay(the.dung())
    except Exception:
        pass
    goi.oauth.don_bo_dem_lam_moi()


def cai_voi_refresh_token(the, may_chu, rt="RT-BAN-DAU-1", expires_in=3600):
    """Cài module với một refresh_token; máy chủ giả trả expires_in cho."""
    # Điều khiển expires_in của lượt đổi ở cai(): vá tạm handler? Đơn giản:
    # cai() đổi RT lấy access_token; expires_at do module tính từ expires_in
    # máy chủ giả trả (28800). Muốn tài khoản «sắp hết» thì chỉnh sau.
    kq = chay(the.cai({"refresh_token": [rt],
                       "co_so_url": may_chu.url_goc}))
    assert kq["ok"], f"cai thất bại: {kq}"
    assert len(the._tai_khoan) == 1
    return the._tai_khoan[0]


# ---------------------------------------------------------------------------
# F1 — Đăng nhập lần đầu PKCE + state
# ---------------------------------------------------------------------------

def test_f1_dang_nhap_pkce_state(mod, may_chu):
    the, goi = mod
    kq = chay(the.thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap", {}))
    assert kq["ok"], kq
    trang_thai = kq["trang_thai_moi"]
    auth_url = trang_thai["auth_url"]
    state = trang_thai["state"]

    # 1. URL đăng nhập mang đủ tham số PKCE S256 + state ngẫu nhiên.
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert qs["response_type"] == ["code"]
    assert "localhost:1455/auth/callback" in qs["redirect_uri"][0]
    assert len(state) == 32  # secrets.token_hex(16)

    # 2. challenge == S256(verifier) thật (không phải chuỗi ngẫu nhiên).
    verifier = the._cho_oauth._cho[state]["code_verifier"]
    challenge_tinh = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert qs["code_challenge"] == [challenge_tinh]

    # 3. Dán URL callback → đổi code qua máy chủ OAuth GIẢ → có tài khoản.
    url_cb = (f"http://localhost:1455/auth/callback"
              f"?code=MA-GIA-1&state={state}")
    kq2 = chay(the.thuc_hien("openaicodex.dang-nhap", "dan_callback",
                             {"url_callback": url_cb}))
    assert kq2["ok"], kq2
    assert kq2["trang_thai_moi"]["co_refresh_token"] is True
    assert may_chu.so_lan_doi_code == 1
    # Thân đổi mã gửi đúng code_verifier + grant authorization_code.
    assert may_chu.than_doi_code_cuoi["grant_type"] == "authorization_code"
    assert may_chu.than_doi_code_cuoi["code_verifier"] == verifier
    assert may_chu.than_doi_code_cuoi["code"] == "MA-GIA-1"
    assert len(the._tai_khoan) == 1
    assert the._tai_khoan[0].refresh_token == "RT-TU-CODE-1"
    assert the._tai_khoan[0].trang_thai == "hoat_dong"

    # 4. State dùng MỘT lần: dán lại cùng state → từ chối, KHÔNG gọi mạng.
    kq3 = chay(the.thuc_hien("openaicodex.dang-nhap", "dan_callback",
                             {"url_callback": url_cb}))
    assert kq3["ok"] is False
    assert "state" in kq3["loi"]
    assert may_chu.so_lan_doi_code == 1, "state đã dùng vẫn gọi đổi mã!"

    # 5. State lạ → từ chối ngay, không gọi mạng.
    url_la = ("http://localhost:1455/auth/callback"
              "?code=MA-GIA-2&state=" + "0" * 32)
    kq4 = chay(the.thuc_hien("openaicodex.dang-nhap", "dan_callback",
                             {"url_callback": url_la}))
    assert kq4["ok"] is False
    assert may_chu.so_lan_doi_code == 1


# ---------------------------------------------------------------------------
# F2 — Làm mới trước hạn
# ---------------------------------------------------------------------------

def test_f2_lam_moi_truoc_han(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    assert may_chu.so_lan_lam_moi == 1  # lượt đổi ở cai()
    # Qua 60 giây cache chống-xoay của cai(): mô phỏng «lâu sau đó».
    goi.oauth.don_bo_dem_lam_moi()
    # Tài khoản «sắp hết» (còn 1 giờ < ngưỡng 6 giờ) → lam_moi_phien làm mới.
    tk.expires_at = time.time() + 3600
    kq = chay(the.lam_moi_phien(tk.khoa))
    assert kq.thanh_cong is True and kq.tang == "a", kq
    assert may_chu.so_lan_lam_moi == 2, "không làm mới khi sắp hết hạn!"
    assert tk.refresh_token == "RT-MOI-2"  # xoay refresh token
    assert tk.expires_at - time.time() > 7 * 3600
    assert tk.trang_thai == "hoat_dong"

    # Tài khoản còn hạn dài → lam_moi_phien KHÔNG gọi mạng.
    kq2 = chay(the.lam_moi_phien(tk.khoa))
    assert kq2.thanh_cong is True
    assert may_chu.so_lan_lam_moi == 2, "làm mới thừa khi token còn hạn!"


# ---------------------------------------------------------------------------
# F3 — Làm mới khi gặp 401
# ---------------------------------------------------------------------------

def test_f3_lam_moi_khi_401(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    access_cu = tk.access_token
    # Codex giả: chỉ Bearer MỚI (sau làm mới) mới 200.
    may_chu.token_codex_hop_le = "Bearer-se-doi-sau-lam-moi"
    # Ép máy chủ giả cấp đúng access token ta chờ ở lượt làm mới.
    # (Máy chủ giả cấp JWT ngẫu nhiên; ta đọc lại sau refresh để cấu hình.)
    body = {"model": "openaicodex/gpt-5.5",
            "messages": [{"role": "user", "content": "chào"}]}
    # Chat chạy trong luồng riêng + bẫy vòng lặp: MỘT lượt chat chỉ được làm
    # mới TỐI ĐA 1 lần cho mỗi tài khoản (kỳ vọng so_lan_lam_moi == 2 = 1 ở
    # cai + 1 ở chat). BUG ĐANG ĐO: khoa = sha256(access_token) nên mỗi lần
    # làm mới thành công khoa lại đổi → da_lam_moi không bao giờ khớp →
    # vòng lặp làm mới vô hạn (treo). Bẫy này biến «treo» thành ĐỎ gọn.
    ket_qua: dict = {}

    def _goi():
        ket_qua["kq"] = chay(the.chat(body, False))

    luong = threading.Thread(target=_goi, daemon=True)
    luong.start()
    han = time.time() + 30
    while luong.is_alive() and time.time() < han:
        if may_chu.so_lan_lam_moi > 4:
            pytest.fail(
                "vòng lặp làm mới vô hạn: "
                f"{may_chu.so_lan_lam_moi - 1} lần làm mới trong MỘT lượt "
                "chat (khoa xoay theo access_token nên da_lam_moi không khớp)")
        time.sleep(0.2)
    assert not luong.is_alive(), "treo: chat() không trả sau 30s"
    ma, _tieu_de, _than = ket_qua["kq"]
    # Lượt 1: 401 → làm mới → thử lại. Máy chủ giả chưa biết token mới nên
    # vẫn 401 → đếm 401 (chưa tới ngưỡng vô hiệu) → trả 401 cho khách.
    assert ma == 401, f"mong 401 sau khi làm mới vẫn trượt, được {ma}"
    assert may_chu.so_lan_lam_moi == 2, "401 mà không làm mới!"
    assert tk.access_token != access_cu, "không áp token mới sau 401"
    assert tk.trang_thai == "hoat_dong", "vô hiệu oan sau một 401 đơn lẻ"
    assert tk.so_401 == 1

    # Lượt 2: cấu hình Codex giả chấp nhận token mới → chat 200.
    may_chu.token_codex_hop_le = tk.access_token
    ma2, _t2, than2 = chay(the.chat(body, False))
    assert ma2 == 200, f"mong 200 sau khi token mới được chấp nhận, được {ma2}"
    assert than2  # thân JSON gộp


# ---------------------------------------------------------------------------
# F4 — Thu hồi / đăng xuất
# ---------------------------------------------------------------------------

def test_f4_thu_hoi_dang_xuat(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    id_tk = tk.id_thuc_the

    # Xoá không xác nhận → bị từ chối, tài khoản còn nguyên.
    kq0 = chay(the.thuc_hien(id_tk, "xoa_tai_khoan", {}))
    assert kq0["ok"] is False
    assert len(the._tai_khoan) == 1

    # Xoá có xác nhận → tài khoản biến mất khỏi bộ nhớ + kho.
    kq = chay(the.thuc_hien(id_tk, "xoa_tai_khoan", {"xac_nhan": "xoa"}))
    assert kq["ok"] is True
    assert the._tai_khoan == []
    du_lieu_kho = the.kho.doc("tai_khoan") or {}
    assert (du_lieu_kho.get("tai_khoan") or []) == []

    # KHÔNG có lời gọi tới endpoint thu hồi nào của máy chủ OAuth
    # (c2a cũng không gọi — đăng xuất ở cả hai phía chỉ là xoá cục bộ).
    # cai() dùng refresh_token grant nên so_lan_doi_code == 0.
    assert may_chu.so_lan_doi_code == 0
    assert may_chu.so_lan_lam_moi == 1  # chỉ lượt ở cai()


# ---------------------------------------------------------------------------
# F5 — invalid_grant → «cần đăng nhập lại»
# ---------------------------------------------------------------------------

def test_f5_invalid_grant_can_dang_nhap_lai(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    # Qua 60 giây cache chống-xoay của cai(): mô phỏng «lâu sau đó».
    goi.oauth.don_bo_dem_lam_moi()
    may_chu.kich_ban = "invalid_grant"
    tk.expires_at = time.time() - 10  # ép hết hạn để lam_moi_phien chạy

    kq = chay(the.lam_moi_phien(tk.khoa))
    assert kq.thanh_cong is False, "invalid_grant mà báo ok là sai"
    assert kq.tang == "a"
    assert kq.ma_loi == "refresh_het_han"
    assert kq.ma_trang_thai == 401

    # Tài khoản bị đánh dấu hỏng (cần đăng nhập lại), KHÔNG thử lại mãi.
    assert tk.trang_thai == "vo_hieu"
    assert may_chu.so_lan_lam_moi == 2, (
        f"invalid_grant phải dừng sau 1 lần, đã gọi {may_chu.so_lan_lam_moi}")

    # Tài khoản hỏng không được chọn cho chat nữa.
    assert the._chon_tai_khoan(set()) is None


# ---------------------------------------------------------------------------
# F6 — Không hai nơi cùng làm mới một tài khoản
# ---------------------------------------------------------------------------

def test_f6_khong_hai_noi_cung_lam_moi(mod, may_chu):
    the, goi = mod
    tk = cai_voi_refresh_token(the, may_chu)
    assert may_chu.so_lan_lam_moi == 1
    # Qua 60 giây cache chống-xoay của cai(): mô phỏng «lâu sau đó».
    goi.oauth.don_bo_dem_lam_moi()
    tk.expires_at = time.time() - 10  # ép hết hạn
    rt = tk.refresh_token

    ket_qua = []
    loi = []

    def _lam():
        try:
            ket_qua.append(chay(the._lam_moi_tai_khoan(tk)))
        except Exception as e:  # noqa: BLE001
            loi.append(e)

    cac_luong = [threading.Thread(target=_lam) for _ in range(10)]
    for t in cac_luong:
        t.start()
    for t in cac_luong:
        t.join(timeout=30)

    assert not loi, f"luồng lỗi: {loi}"
    assert len(ket_qua) == 10
    assert all(k.get("ok") for k in ket_qua), ket_qua
    # Mười lượt đồng thời → CHỈ MỘT POST đổi refresh_token (xoay token sẽ
    # làm mất hiệu lực lượt còn lại nếu POST hai lần).
    assert may_chu.so_lan_lam_moi == 2, (
        f"đua làm mới: {may_chu.so_lan_lam_moi - 1} POST cho 10 lượt!")
    assert tk.refresh_token == "RT-MOI-2"
    assert tk.refresh_token != rt  # refresh token đã xoay

