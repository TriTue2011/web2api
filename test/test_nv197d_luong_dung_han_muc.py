"""NV197d — theo dõi lượng dùng + hạn mức theo tài khoản AI.

ĐỎ→XANH QUA LÕI THẬT: đăng ký module từ xa trỏ vào nhà cung cấp GIẢ chạy HTTP
thật (uvicorn), chat qua /v1/chat/completions của lõi, sổ lượng dùng ghi ở điểm
chung app/chuyen_tiep.py::_ghi_so_lieu (không chép vào từng module).
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.main import app
from gia import MayChuThat

KHOA_QT = "khoa-qt-nv197d-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
NOI_DUNG_BI_MAT = "noi-dung-hoi-thoai-bi-mat-xyz-197d"


class NhaCungCapGia:
    """Nhà cung cấp OpenAI GIẢ chạy HTTP thật.

    che_do="co_han_muc": gửi usage + tiêu đề x-ratelimit-*.
    che_do="khong": không gửi tiêu đề hạn mức nào (lõi không được bịa số).
    che_do="429": trả 429 + retry-after.
    che_do="codex": gửi họ tiêu đề x-codex-* như Codex thật.
    """

    def __init__(self, che_do="co_han_muc"):
        self.che_do = che_do
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        return JSONResponse({"object": "list", "data": [{"id": "m", "object": "model"}]})

    async def chat(self, req):
        if self.che_do == "429":
            return JSONResponse({"error": {"message": "slow down"}}, status_code=429,
                                headers={"retry-after": "120"})
        headers = {}
        if self.che_do == "co_han_muc":
            headers = {"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "987",
                       "x-ratelimit-reset": str(int(time.time()) + 3600)}
        elif self.che_do == "codex":
            headers = {"x-codex-primary-used-percent": "80",
                       "x-codex-primary-window-minutes": "300",
                       "x-codex-primary-reset-after-seconds": "300"}
        return JSONResponse(
            {"object": "chat.completion", "model": "m",
             "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": NOI_DUNG_BI_MAT}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
            headers=headers)


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def xa():
    gia = NhaCungCapGia()
    with MayChuThat(gia.app) as s:
        gia.url = s.url
        yield gia


def _them_tai_khoan(client, email):
    r = client.post("/api/tai-khoan/da-luu", headers=QT,
                    json={"email": email, "mat_khau": "MatKhauGia123!"})
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]["id"]


def _dang_ky_xa(client, xa, tk_id, tien_to="xa"):
    r = client.post("/api/modules/tu-xa", headers=QT, json={
        "id": "gia-" + tien_to, "ten": "Xa " + tien_to, "tien_to": tien_to,
        "base_url": xa.url + "/v1", "api_key": "<redacted>", "tai_khoan_id": tk_id})
    assert r.status_code == 200, r.text
    return r


def _chat(client, tien_to="xa"):
    return client.post("/v1/chat/completions", headers=QT, json={
        "model": tien_to + "/m", "messages": [{"role": "user", "content": "chào"}]})


# ── (1) chat qua lõi thật → sổ ghi lượt + token + hạn mức ────────────────────

def test_chat_qua_loi_that_ghi_luot_token_han_muc(moi_truong, xa):
    """Lõi thật + nhà cung cấp giả gửi header: sổ ghi 1 lượt, token vào/ra, hạn mức còn lại."""
    tk_id = _them_tai_khoan(moi_truong, "dung197d@example.com")
    _dang_ky_xa(moi_truong, xa, tk_id)
    r = _chat(moi_truong)
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == NOI_DUNG_BI_MAT

    from app import luong_dung_tai_khoan as ldtk
    tk = ldtk.thong_ke(tk_id)
    assert tk["tong"]["luot"] == 1
    assert tk["tong"]["token_vao"] == 10
    assert tk["tong"]["token_ra"] == 20

    hm = ldtk.lay_han_muc(tk_id)
    assert hm is not None and hm["con_lai"] == 987, hm
    assert hm["nguon"] == "header"

    # ban_cong_khai có dung + han_muc (đỏ của NV197a: test_theo_doi_han_muc).
    ds = moi_truong.get("/api/tai-khoan/da-luu", headers=QT).json()["tai_khoan"]
    bk = [t for t in ds if t["id"] == tk_id][0]
    assert "han_muc" in bk and bk["han_muc"] != "khong_biet"
    assert bk["dung"]["hom_nay"] == 1 and bk["dung"]["7_ngay"] == 1

    # Route han-muc (đỏ của NV197a).
    r2 = moi_truong.get("/api/tai-khoan/han-muc", headers=QT)
    assert r2.status_code == 200
    muc = {t["id"]: t["han_muc"] for t in r2.json()["tai_khoan"]}
    assert muc[tk_id]["con_lai"] == 987


def test_luong_dung_route_co_phan_trang_va_khoang_ngay(moi_truong, xa):
    """GET /api/tai-khoan/luong-dung: phân trang + khoảng ngày (đỏ của NV197a)."""
    tk_id = _them_tai_khoan(moi_truong, "trang197d@example.com")
    _dang_ky_xa(moi_truong, xa, tk_id)
    assert _chat(moi_truong).status_code == 200

    r = moi_truong.get(f"/api/tai-khoan/luong-dung?tai_khoan_id={tk_id}", headers=QT)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tong"]["luot"] == 1 and j["tong_hang"] == 1
    assert len(j["ngay"]) == 1 and j["trang"] == 1

    # Khoảng ngày loại hết → 0 hàng nhưng tong vẫn tổng (đúng ngữ nghĩa).
    r2 = moi_truong.get(f"/api/tai-khoan/luong-dung?tai_khoan_id={tk_id}&tu_ngay=2000-01-01&den_ngay=2000-01-02",
                        headers=QT)
    assert r2.status_code == 200 and r2.json()["ngay"] == []

    # Không id → 200 tổng quan mọi tài khoản; id lạ → 404; không khoá quản trị → 401.
    r0 = moi_truong.get("/api/tai-khoan/luong-dung", headers=QT)
    assert r0.status_code == 200, r0.text
    assert r0.json()["tong_hang"] >= 1
    assert moi_truong.get("/api/tai-khoan/luong-dung?tai_khoan_id=khong-co", headers=QT).status_code == 404
    assert moi_truong.get("/api/tai-khoan/luong-dung?tai_khoan_id=x").status_code == 401


# ── (2) không bịa số khi nhà cung cấp không gửi tiêu đề ─────────────────────

def test_khong_bia_han_muc_khi_thieu_header(moi_truong):
    """Nhà cung cấp giả KHÔNG gửi tiêu đề hạn mức → hiển thị «khong_biet», không bịa."""
    gia = NhaCungCapGia(che_do="khong")
    with MayChuThat(gia.app) as s:
        gia.url = s.url
        tk_id = _them_tai_khoan(moi_truong, "khongbiet197d@example.com")
        _dang_ky_xa(moi_truong, gia, tk_id, tien_to="xb")
        assert _chat(moi_truong, tien_to="xb").status_code == 200

    from app import luong_dung_tai_khoan as ldtk
    assert ldtk.lay_han_muc(tk_id) is None
    ds = moi_truong.get("/api/tai-khoan/da-luu", headers=QT).json()["tai_khoan"]
    bk = [t for t in ds if t["id"] == tk_id][0]
    assert bk["han_muc"] == "khong_biet"
    # Lượng dùng vẫn ghi (không phụ thuộc header).
    assert ldtk.thong_ke(tk_id)["tong"]["luot"] == 1


def test_header_codex_duoc_doc_thu_dong(moi_truong):
    """Tiêu đề hạn mức kiểu Codex (x-codex-*) được đọc thụ động như c2a ghi_nhan_tu_header."""
    gia = NhaCungCapGia(che_do="codex")
    with MayChuThat(gia.app) as s:
        gia.url = s.url
        tk_id = _them_tai_khoan(moi_truong, "codex197d@example.com")
        _dang_ky_xa(moi_truong, gia, tk_id, tien_to="xc")
        assert _chat(moi_truong, tien_to="xc").status_code == 200

    from app import luong_dung_tai_khoan as ldtk
    hm = ldtk.lay_han_muc(tk_id)
    assert hm is not None and hm["codex_primary"]["da_dung_pct"] == 80.0, hm


# ── (3) hết hạn mức → đánh dấu + lõi bỏ qua đúng đến lúc đặt lại ─────────────

def test_429_danh_dau_het_han_muc_loi_bo_qua(moi_truong, monkeypatch):
    """429 + retry-after qua lõi thật → tài khoản bị đánh dấu, thu_dang_nhap_lai bỏ qua."""
    from app import luong_dung_tai_khoan as ldtk
    from app import kho_tai_khoan as ktk

    gia = NhaCungCapGia(che_do="429")
    with MayChuThat(gia.app) as s:
        gia.url = s.url
        email = "het197d@example.com"
        tk_id = _them_tai_khoan(moi_truong, email)
        _dang_ky_xa(moi_truong, gia, tk_id, tien_to="xd")
        r = _chat(moi_truong, tien_to="xd")
        assert r.status_code == 429, r.text

    den = ldtk.con_het_han_muc(tk_id)
    assert den > time.time(), "429 phải đánh dấu het_han_muc_den trong tương lai"
    tk = ldtk.thong_ke(tk_id)
    assert tk["tong"]["loi_han_muc"] == 1

    class ModGia:
        async def dang_nhap(self, dong):
            raise AssertionError("tài khoản hết hạn mức không được gọi module")

    import asyncio
    kq = asyncio.run(ktk.thu_dang_nhap_lai(module=ModGia(), module_id="m", dinh_danh=email))
    assert kq == {"thu": False, "ly_do": "het_han_muc"}, kq

    # Hết giờ đánh dấu → lõi lại dùng tài khoản bình thường.
    from types import SimpleNamespace
    monkeypatch.setattr(ldtk, "_dong_ho", lambda: den + 1)

    class ModGia2:
        async def dang_nhap(self, dong):
            return SimpleNamespace(thanh_cong=True)

    kq2 = asyncio.run(ktk.thu_dang_nhap_lai(module=ModGia2(), module_id="m", dinh_danh=email))
    assert kq2 == {"thu": True, "thanh_cong": True}, kq2


# ── (4) phân loại lỗi theo mã HTTP, không dò chữ ─────────────────────────────

def test_phan_loai_loi_chi_theo_ma_http():
    from app.luong_dung_tai_khoan import phan_loai_loi
    assert phan_loai_loi(429) == "han_muc"
    assert phan_loai_loi(401) == "het_phien"
    assert phan_loai_loi(403) == "het_phien"
    assert phan_loai_loi(400) == "khac"
    assert phan_loai_loi(500) == "khac"
    assert phan_loai_loi(200) == "khac"


def test_loi_ghi_dung_loai_theo_ma(moi_truong):
    """401 → loi_het_phien; 400 có chữ «rate limit» trong thân → vẫn loi_khac (không dò chữ)."""
    from app import luong_dung_tai_khoan as ldtk
    tk_id = _them_tai_khoan(moi_truong, "loi197d@example.com")
    assert ldtk.ghi_luot(tk_id, ma=401)
    assert ldtk.ghi_luot(tk_id, ma=400)
    assert ldtk.ghi_luot(tk_id, ma=500)
    tong = ldtk.thong_ke(tk_id)["tong"]
    assert tong["loi_het_phien"] == 1 and tong["loi_khac"] == 2 and tong["loi_han_muc"] == 0


# ── (5) an toàn: không ghi nội dung hội thoại / bí mật; sổ không bao giờ ném ─

def test_khong_luu_noi_dung_hoi_thoai_va_bi_mat(moi_truong, xa, tmp_path):
    tk_id = _them_tai_khoan(moi_truong, "antoan197d@example.com")
    _dang_ky_xa(moi_truong, xa, tk_id)
    assert _chat(moi_truong).status_code == 200
    noi = (tmp_path / "luong_dung_tai_khoan.json").read_text(encoding="utf-8")
    assert NOI_DUNG_BI_MAT not in noi, "sổ không được chứa nội dung hội thoại"
    assert "<redacted>" not in noi and "khoa-gia" not in noi


def test_so_khong_bao_gio_nem(moi_truong):
    """Đầu vào hỏng → ghi_luot trả False, không ném; đường chat không bị ảnh hưởng."""
    from app import luong_dung_tai_khoan as ldtk
    assert ldtk.ghi_luot(None) is False
    assert ldtk.ghi_luot("") is False
    assert ldtk.ghi_luot(123) is False
    assert ldtk.ghi_luot("x" * 200) is False
    assert ldtk.thong_ke("id-la-khong-co") is None
    assert ldtk.lay_han_muc("id-la-khong-co") is None
    assert ldtk.con_het_han_muc("id-la-khong-co") == 0
    # Header hỏng đủ kiểu vẫn không ném.
    assert ldtk.doc_han_muc_tu_header(None) is None
    assert ldtk.doc_han_muc_tu_header({"x-ratelimit-remaining": "khong-phai-so"}) is None
    assert ldtk.doc_han_muc_tu_header({"x-ratelimit-remaining": float("nan")}) is None
    assert ldtk.ghi_luot("tk-ok", headers=object()) is True


# ── (6) sổ bền: ghi nguyên tử, trần kích thước, dọn cũ ───────────────────────

def test_so_ben_va_co_tran(moi_truong, tmp_path):
    import datetime as _dt
    from app import luong_dung_tai_khoan as ldtk
    tk_id = _them_tai_khoan(moi_truong, "ben197d@example.com")
    goc = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc).timestamp()
    dong_ho_cu = ldtk._dong_ho
    try:
        # 95 ngày liên tiếp → dọn còn 90.
        for i in range(95):
            ldtk._dong_ho = (lambda g: (lambda: g))(goc + i * 86400)
            assert ldtk.ghi_luot(tk_id)
    finally:
        ldtk._dong_ho = dong_ho_cu
    du_lieu = json.loads((tmp_path / "luong_dung_tai_khoan.json").read_text(encoding="utf-8"))
    ngay = du_lieu["tai_khoan"][tk_id]["ngay"]
    assert len(ngay) == 90, len(ngay)
    # Khởi động lại (đọc lại từ đĩa) vẫn còn số liệu: 90 ngày × 1 lượt.
    tk = ldtk.thong_ke(tk_id)
    assert tk["tong"]["luot"] == 90


# ── (7) đăng ký tu_xa: tai_khoan_id lạ → 400; hợp lệ → lưu vào bản ghi ───────

def test_dang_ky_tu_xa_tai_khoan_id_la_bi_400(moi_truong, xa):
    r = moi_truong.post("/api/modules/tu-xa", headers=QT, json={
        "id": "gia-la", "ten": "Xa lạ", "tien_to": "xla",
        "base_url": xa.url + "/v1", "api_key": "<redacted>",
        "tai_khoan_id": "khong-co-trong-kho"})
    assert r.status_code == 400, r.text
    assert "tai_khoan_id" in r.text


def test_dang_ky_tu_xa_luu_tai_khoan_id_vao_ban_ghi(moi_truong, xa, tmp_path):
    tk_id = _them_tai_khoan(moi_truong, "lienket197d@example.com")
    _dang_ky_xa(moi_truong, xa, tk_id, tien_to="xe")
    ban = json.loads((tmp_path / "tu_xa.json").read_text(encoding="utf-8"))
    rec = [m for m in ban["modules"] if m["id"] == "gia-xe"][0]
    assert rec["tai_khoan_id"] == tk_id
