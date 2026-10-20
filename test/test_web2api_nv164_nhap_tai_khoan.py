"""Test module nhaptaikhoan (NV164): nhập hàng loạt tài khoản — PHẦN ĐỌC/XEM TRƯỚC.

Phạm vi: phân tích tệp sao lưu 9router (JSON / JSON.gz) + xem trước qua API
quản trị của máy chủ sub2api / CLIProxyAPI (CPA), mọi bí mật chỉ hiện DẠNG CHE.
CHƯA nối kho tài khoản (NV144/NV72): hành động `ap_dung` từ chối rõ ràng.

Máy chủ sub2api/CPA GIẢ dựng bằng thư viện chuẩn (http.server cục bộ);
không mạng thật. Bí mật trong test là chuỗi GIẢ rõ ràng.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "nhaptaikhoan"

# Chuỗi GIẢ rõ ràng — không phải bí mật thật.
API_KEY_GIA = "sub2api-api-key-gia-nv164-0123456789abcdef"
MAT_KHAU_GIA = "mat-khau-gia-nv164"
EMAIL_GIA = "nguoi.a@example.com"
CPA_KEY_GIA = "cpa-secret-gia-nv164-xyz"
TOKEN_9ROUTER_GIA = "eyJhbGciOiJSUzI1NiJ9.MOHINH-GIA-NV164.codex-token"


def _module_py():
    p = THU_MUC / "module.py"
    assert p.is_file(), "chưa có module.py — test đỏ đúng thiết kế (đỏ trước)"
    spec = importlib.util.spec_from_file_location("nhaptaikhoan_nv164", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def mod(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({})
    return m


# ───────── Ngữ cảnh gọi (app.ngu_canh) ─────────

@pytest.fixture()
def ctx_quan_tri():
    from app import ngu_canh
    tok = ngu_canh.dat({"loai": "quan_tri", "id": "qt-test", "pham_vi": ["*"]}, nguon="nguoi")
    try:
        yield
    finally:
        ngu_canh.khoi_phuc(tok)


@pytest.fixture()
def ctx_khach():
    from app import ngu_canh
    tok = ngu_canh.dat({"loai": "khach", "id": "k1", "pham_vi": ["thiet_bi:ghi"]}, nguon="nguoi")
    try:
        yield
    finally:
        ngu_canh.khoi_phuc(tok)


def _thuc_hien(mod, hanh_dong, tham_so):
    return asyncio.run(mod.thuc_hien("nhaptaikhoan.nguon", hanh_dong, tham_so))


# ───────── manifest ─────────

def test_manifest_hop_le(md):
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "nhaptaikhoan"
    assert manifest["tien_to"] == "nhaptaikhoan"
    assert manifest["hang"] == "A"
    assert "tai_lieu" in manifest["cung_cap"]
    assert manifest["yeu_cau_goi"] == ["httpx"]
    assert isinstance(manifest["truong_cau_hinh"], list)
    assert manifest["tai_nguyen"]["mang_ra"] == []


def test_cai_khong_can_cau_hinh(mod):
    kq = asyncio.run(mod.cai({}))
    assert kq["ok"] is True
    assert kq["loi"] is None
    assert kq["cau_hinh_luu"] == {}


def test_suc_khoe_khong_lo_bi_mat(mod):
    kq = asyncio.run(mod.suc_khoe())
    assert kq["ok"] is True
    for khoa in ("khoa_api", "mat_khau", "token", "access_token"):
        assert khoa not in json.dumps(kq, ensure_ascii=False).lower()


def test_thuc_the_nguy_hiem_mac_dinh(mod):
    ents = {e["id"]: e for e in mod.thuc_the()}
    ent = ents["nhaptaikhoan.nguon"]
    assert ent["rui_ro_mac_dinh"] == "nguy_hiem"
    hanh_dong = {a["hanh_dong"]: a for a in ent["tinh_nang"]}
    assert set(hanh_dong) == {"xem_truoc_tep", "xem_truoc_may_chu", "ap_dung"}
    for a in hanh_dong.values():
        assert a["rui_ro"] == "nguy_hiem"


# ───────── Cổng quản trị: chỉ quản trị gọi được ─────────

def test_thieu_ngu_canh_bi_tu_choi(mod):
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": "e30="})
    assert kq["ok"] is False and kq["loi"] == "chi_quan_tri"


def test_khach_bi_tu_choi(mod, ctx_khach):
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": "e30="})
    assert kq["ok"] is False and kq["loi"] == "chi_quan_tri"


def test_quan_tri_duoc_qua_cong(mod, ctx_quan_tri):
    # Qua cổng quản trị rồi mới tới lỗi nghiệp vụ (ở đây: base64 hợp lệ nhưng không phải backup).
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": base64.b64encode(b"{}").decode()})
    assert kq["loi"] != "chi_quan_tri"


def test_hanh_dong_la_bi_tu_choi(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xoa_het", {})
    assert kq["ok"] is False and kq["loi"] == "hanh_dong_khong_ho_tro"


# ───────── che ─────────

def test_che_email(md):
    assert md._che_email("nguoi.a@example.com") == "n***@example.com"
    assert md._che_email("a@x.io") == "a***@x.io"
    assert md._che_email("khong-co-a-cong") == "***"
    assert md._che_email("") == "***"


def test_che_khoa(md):
    assert md._che_khoa("abcdef0123456789") == "…6789"
    assert md._che_khoa("ab") == "…"
    assert md._che_khoa("") == "…"


# ───────── Dữ liệu 9router giả ─────────

def _backup_9router():
    return {
        "settings": {"theme": "dark"},
        "providerConnections": [
            {"provider": "codex", "connectionName": "Codex chính",
             "data": {"accessToken": TOKEN_9ROUTER_GIA, "refreshToken": "refresh-gia-nv164",
                      "expiresAt": "2027-01-01T00:00:00Z",
                      "email": "nguoi.a@example.com"}},
            {"provider": "claude", "connectionName": "Claude work",
             "data": {"accessToken": "sk-ant-gia-nv164", "email": "nguoi.b@example.com"}},
            {"provider": "codex", "connectionName": "Codex không token", "data": {}},
        ],
        "combos": [{"name": "combo-gia", "models": ["a", "b"]}],
    }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_xem_truoc_tep_9router_json(mod, ctx_quan_tri):
    noi_dung = json.dumps(_backup_9router(), ensure_ascii=False).encode("utf-8")
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": _b64(noi_dung)})
    assert kq["ok"] is True
    assert kq["nguon"] == "ninerouter"
    assert kq["tong"] == 3
    assert kq["so_combo"] == 1
    tk = {t["ten"]: t for t in kq["tai_khoan"]}
    codex = tk["Codex chính"]
    assert codex["nha_cung_cap"] == "codex"
    assert codex["co_the_nhap"] is True
    # Provider ngoài danh sách nhập được (claude) + token không phải eyJ → không nhập được:
    claude = tk["Claude work"]
    assert claude["co_the_nhap"] is False
    assert claude["email_che"] == "n***@example.com"
    assert codex["email_che"] == "n***@example.com"
    assert codex["co_refresh"] is True
    # KHÔNG bí mật thô nào trong phản hồi:
    tho = json.dumps(kq, ensure_ascii=False)
    assert TOKEN_9ROUTER_GIA not in tho
    assert "refresh-gia-nv164" not in tho
    assert "nguoi.a@example.com" not in tho
    assert "sk-ant-gia-nv164" not in tho


def test_xem_truoc_tep_9router_json_gz(mod, ctx_quan_tri):
    noi_dung = gzip.compress(json.dumps(_backup_9router()).encode("utf-8"))
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": _b64(noi_dung)})
    assert kq["ok"] is True
    assert kq["dinh_dang"] == "json.gz"
    assert kq["tong"] == 3


def test_xem_truoc_tep_dang_long(mod, ctx_quan_tri):
    # Dạng lồng data.providerConnections của 9router.
    dulieu = {"data": {"providerConnections": [{"provider": "codex", "name": "c1",
                                                "data": {"accessToken": TOKEN_9ROUTER_GIA}}]}}
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter",
                                           "noi_dung_base64": _b64(json.dumps(dulieu).encode())})
    assert kq["ok"] is True and kq["tong"] == 1


def test_xem_truoc_tep_khong_phai_backup(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter",
                                          "noi_dung_base64": _b64(b'{"hello": 1}')})
    assert kq["ok"] is False and kq["loi"] == "dinh_dang_khong_ho_tro"


def test_xem_truoc_tep_base64_sai(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": "!!!khong-phai-base64!!!"})
    assert kq["ok"] is False and kq["loi"] == "noi_dung_sai"
    # Ký tự rác chen vào một chuỗi base64 HỢP LỆ cũng phải bị chặn bởi
    # validate=True — bỏ ký tự rác rồi giải mã là chấp nhận dữ liệu bẩn:
    hop_le = _b64(json.dumps(_backup_9router()).encode())
    kq2 = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": hop_le + "!!!"})
    assert kq2["ok"] is False and kq2["loi"] == "noi_dung_sai"


def test_xem_truoc_tep_gz_hong(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter",
                                          "noi_dung_base64": _b64(b"\x1f\x8b" + b"khong-phai-gzip")})
    assert kq["ok"] is False and kq["loi"] == "noi_dung_sai"


def test_xem_truoc_tep_qua_lon(mod, ctx_quan_tri):
    lon = _b64(b"x" * (9 * 1024 * 1024))
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": lon})
    assert kq["ok"] is False and kq["loi"] == "tep_qua_lon"


def test_xem_truoc_tep_gz_bom_no(mod, ctx_quan_tri):
    # Gzip bomb: nén nhỏ nhưng bung ra quá trần → từ chối.
    bom = gzip.compress(b"0" * (12 * 1024 * 1024))
    assert len(bom) < 2 * 1024 * 1024
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter", "noi_dung_base64": _b64(bom)})
    assert kq["ok"] is False and kq["loi"] == "tep_qua_lon"


def test_xem_truoc_tep_thieu_tham_so(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xem_truoc_tep", {"dang": "ninerouter"})
    assert kq["ok"] is False


# ───────── Máy chủ sub2api / CPA GIẢ ─────────

_TK = [
    {"id": 11, "name": "TK một", "status": "active",
     "credentials": {"access_token": "token-sub2api-gia-1", "refresh_token": "rt1",
                     "email": "nguoi.a@example.com", "plan_type": "plus",
                     "expires_at": "2027-01-01T00:00:00Z"}},
    {"id": 12, "name": "TK hai", "status": "active",
     "credentials": {"access_token": "token-sub2api-gia-2",
                     "email": "nguoi.b@example.com", "plan_type": "team"}},
]


class _FakeHandler(BaseHTTPRequestHandler):
    khoa_dung = {"api_key": API_KEY_GIA, "email": EMAIL_GIA, "mat_khau": MAT_KHAU_GIA,
                 "cpa": CPA_KEY_GIA}
    so_luot_tai_khoan = 0
    so_luot_download = 0

    def _gui(self, ma, obj):
        than = json.dumps(obj).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(than)))
        self.end_headers()
        self.wfile.write(than)

    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path == "/api/v1/auth/login":
            dai = int(self.headers.get("Content-Length", 0))
            than = json.loads(self.rfile.read(dai) or b"{}")
            if than.get("email") == self.khoa_dung["email"] and than.get("password") == self.khoa_dung["mat_khau"]:
                self._gui(200, {"code": 200, "message": "ok",
                                "data": {"access_token": "jwt-gia-nv164", "expires_in": 3600}})
            else:
                self._gui(401, {"code": 401, "message": "sai"})
        else:
            self._gui(404, {})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/v1/admin/accounts":
            _FakeHandler.so_luot_tai_khoan += 1
            hop_le = (self.headers.get("x-api-key") == self.khoa_dung["api_key"]
                      or self.headers.get("Authorization") == "Bearer jwt-gia-nv164")
            if not hop_le:
                self._gui(401, {"code": 401, "message": "sai"})
                return
            self._gui(200, {"code": 200, "message": "ok",
                            "data": {"items": _TK, "total": len(_TK)}})
        elif u.path == "/v0/management/auth-files":
            if self.headers.get("Authorization") != f"Bearer {self.khoa_dung['cpa']}":
                self._gui(401, {"error": "sai"})
                return
            self._gui(200, {"files": [{"name": "acc1.json", "email": "nguoi.c@example.com"},
                                      {"name": "acc2.json", "email": "nguoi.d@example.com"}]})
        elif u.path == "/v0/management/auth-files/download":
            _FakeHandler.so_luot_download += 1
            self._gui(200, {"access_token": "token-cpa-gia-bi-mat"})
        else:
            self._gui(404, {})


@pytest.fixture()
def may_chu_gia(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    srv = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ───────── xem_truoc_may_chu: sub2api ─────────

def test_sub2api_xem_truoc_bang_api_key(mod, ctx_quan_tri, may_chu_gia):
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": may_chu_gia, "khoa_api": API_KEY_GIA})
    assert kq["ok"] is True
    assert kq["nguon"] == "sub2api"
    assert kq["tong"] == 2
    tk = {t["id"]: t for t in kq["tai_khoan"]}
    assert tk["11"]["email_che"] == "n***@example.com"
    assert tk["11"]["goi"] == "plus"
    assert tk["11"]["co_refresh"] is True
    tho = json.dumps(kq, ensure_ascii=False)
    assert "token-sub2api-gia-1" not in tho
    assert API_KEY_GIA not in tho
    assert "nguoi.a@example.com" not in tho


def test_sub2api_xem_truoc_bang_dang_nhap(mod, ctx_quan_tri, may_chu_gia):
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": may_chu_gia,
                     "email": EMAIL_GIA, "mat_khau": MAT_KHAU_GIA})
    assert kq["ok"] is True and kq["tong"] == 2
    assert "jwt-gia-nv164" not in json.dumps(kq, ensure_ascii=False)


def test_sub2api_sai_dang_nhap_khong_lo_mat_khau(mod, ctx_quan_tri, may_chu_gia):
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": may_chu_gia,
                     "email": EMAIL_GIA, "mat_khau": "sai-mat-khau-nv164"})
    assert kq["ok"] is False and kq["loi"] == "sai_dang_nhap"
    assert "sai-mat-khau-nv164" not in json.dumps(kq, ensure_ascii=False)


def test_sub2api_api_key_sai(mod, ctx_quan_tri, may_chu_gia):
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": may_chu_gia, "khoa_api": "sai-key"})
    assert kq["ok"] is False and kq["loi"] == "sai_dang_nhap"


def test_sub2api_url_bi_chan(mod, ctx_quan_tri, monkeypatch):
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": "http://127.0.0.1:9", "khoa_api": API_KEY_GIA})
    assert kq["ok"] is False and kq["loi"] == "url_khong_hop_le"


def test_sub2api_url_sai_khuon(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": "ftp://x/y", "khoa_api": API_KEY_GIA})
    assert kq["ok"] is False and kq["loi"] == "url_khong_hop_le"


def test_sub2api_thieu_xac_thuc(mod, ctx_quan_tri, may_chu_gia):
    kq = _thuc_hien(mod, "xem_truoc_may_chu", {"loai": "sub2api", "may_chu_url": may_chu_gia})
    assert kq["ok"] is False and kq["loi"] == "thieu_xac_thuc"


# ───────── xem_truoc_may_chu: CPA ─────────

def test_cpa_xem_truoc_khong_tai_token(mod, ctx_quan_tri, may_chu_gia):
    _FakeHandler.so_luot_download = 0
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "cpa", "may_chu_url": may_chu_gia, "khoa_api": CPA_KEY_GIA})
    assert kq["ok"] is True
    assert kq["nguon"] == "cpa"
    assert kq["tong"] == 2
    assert kq["tai_khoan"][0] == {"ten": "acc1.json", "email_che": "n***@example.com"}
    # Xem trước KHÔNG tải token (đường download của CPA là phía «áp dụng», chưa nối):
    assert _FakeHandler.so_luot_download == 0
    tho = json.dumps(kq, ensure_ascii=False)
    assert CPA_KEY_GIA not in tho
    assert "nguoi.c@example.com" not in tho


def test_cpa_khoa_sai(mod, ctx_quan_tri, may_chu_gia):
    kq = _thuc_hien(mod, "xem_truoc_may_chu",
                    {"loai": "cpa", "may_chu_url": may_chu_gia, "khoa_api": "sai"})
    assert kq["ok"] is False and kq["loi"] == "sai_dang_nhap"


def test_loai_may_chu_la(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "xem_truoc_may_chu", {"loai": "khong-co", "may_chu_url": "http://x"})
    assert kq["ok"] is False


# ───────── ap_dung: giao diện rõ, từ chối khi chưa nối ─────────

def test_ap_dung_tu_choi_ro_rang(mod, ctx_quan_tri):
    ds = [{"nguon": "sub2api", "dinh_danh": "11", "email_che": "n***@example.com",
           "bi_mat": {"access_token": "token-tho"}}]
    kq = _thuc_hien(mod, "ap_dung", {"danh_sach": ds})
    assert kq["ok"] is False
    assert kq["loi"] == "chua_noi_kho_tai_khoan"
    assert "NV144" in kq["chi_tiet"] or "NV72" in kq["chi_tiet"]


def test_ap_dung_ham_giao_dien(mod, ctx_quan_tri):
    # Hàm giao diện rõ, gọi trực tiếp cũng từ chối rõ ràng.
    kq = mod.ap_dung([{"nguon": "ninerouter", "dinh_danh": "c1"}])
    assert kq["ok"] is False and kq["loi"] == "chua_noi_kho_tai_khoan"


def test_ap_dung_danh_sach_rong(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "ap_dung", {"danh_sach": []})
    assert kq["ok"] is False


def test_ap_dung_khong_quan_tri_bi_tu_choi(mod, ctx_khach):
    kq = _thuc_hien(mod, "ap_dung", {"danh_sach": [{"nguon": "x", "dinh_danh": "y"}]})
    assert kq["ok"] is False and kq["loi"] == "chi_quan_tri"


# ───────── Bộ lọc log: bí mật không vào log httpx ─────────

def test_loc_log_chan_bearer(mod, caplog):
    import logging
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                            "gửi Authorization: Bearer " + API_KEY_GIA, None, None)
    for ten in ("httpx", "httpcore"):
        lg = logging.getLogger(ten)
        for h in lg.handlers:
            pass
    lg = logging.getLogger("httpx")
    ket_qua = all(f.filter(rec) for f in lg.filters if getattr(f, "_nhaptaikhoan_loc", False))
    assert ket_qua is False


def test_loc_log_giu_dong_thuong(mod):
    import logging
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: GET http://x \"200 OK\"", None, None)
    lg = logging.getLogger("httpx")
    ket_qua = all(f.filter(rec) for f in lg.filters if getattr(f, "_nhaptaikhoan_loc", False))
    assert ket_qua is True


def test_nap_lai_giu_bo_loc_moi(mod, md):
    # Nạp lại module dưới tên gói khác (khuôn bug NV116): bộ lọc của bản cũ vẫn chặn.
    import importlib.util
    import logging
    spec2 = importlib.util.spec_from_file_location("ban_cuop_nv164", THU_MUC / "module.py")
    md2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(md2)
    lg = logging.getLogger("httpx")
    locs = [f for f in lg.filters if getattr(f, "_nhaptaikhoan_loc", False)]
    assert len(locs) >= 1
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                            "Authorization: Bearer " + API_KEY_GIA, None, None)
    assert all(not f.filter(rec) for f in locs)
