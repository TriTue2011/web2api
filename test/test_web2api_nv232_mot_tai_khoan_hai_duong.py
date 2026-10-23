"""NV232 — Một tài khoản ChatGPT, HAI đường dùng: Codex TRƯỚC, hết hạn mức
thì sang WEB của CHÍNH tài khoản đó trong cùng lượt.

Đo theo spec (lõi thật TestClient + nhà cung cấp giả):
(1) A có cả khoá Codex lẫn khoá web → lượt đi Codex trước.
(2) Codex hết hạn mức (429 + mốc đặt lại) → TỰ chuyển web của chính A trong
    cùng lượt, không báo lỗi cho khách; trạng thái codex ghi het_han_muc + mốc.
(3) Qua mốc hồi → lượt sau lại Codex (het_han_muc quá mốc tự về san_sang).
(4) Codex lỗi vĩnh viễn (401) ≠ hết hạn mức → can_dang_nhap_lai, lượt sau
    KHÔNG thử lại Codex.
(5) Cả hai đường của A hết (codex 429, web bị chặn bot) → sang tài khoản kế B;
    lỗi chặn bot KHÔNG ghi hỏng trạng thái đường web.
(6) Liên kết Codex cho tài khoản free chưa xác minh điện thoại: thất bại CÓ
    CẤU TRÚC → can_xac_minh_dien_thoai (cần người), web vẫn dùng được; nút
    «Thử liên kết Codex lại» chạy được; hoàn tất đúng email → san_sang.
(7) GET /api/tai-khoan/duong-dung: hạn mức + «đang dùng» THEO TỪNG ĐƯỜNG
    (khong_biet khi chưa có số liệu theo đường — không bịa).
(8) Không lộ mật khẩu/TOTP/token trong mọi phản hồi.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv232-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_GOC = Path(__file__).resolve().parent.parent
_MK = "MatKhauGia232!"
_TOTP = "JBSWY3DPEHPK3PXP"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    """Lõi THẬT: app đầy đủ + SoModule rỗng, dữ liệu cô lập trong tmp."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


def _tao_tai_khoan(email, *, dich_dung="web", dung_cho=("web", "codex")):
    """Tạo tài khoản chatgpt/web trong kho (đường web sẵn sàng theo đích chính)."""
    from app import kho_tai_khoan as k

    return k.luu_tai_khoan(
        email, _MK, _TOTP, nhom="chatgpt", loai_tai_khoan="web",
        dich_dung=dich_dung, dung_cho=list(dung_cho))


def _trang_thai(tk_id):
    from app import kho_tai_khoan as k

    return k.lay_trang_thai_duong(tk_id)


def _dat(tk_id, duong, trang_thai, moc=None):
    from app import kho_tai_khoan as k

    return k.dat_trang_thai_duong(tk_id, duong, trang_thai, moc)


def _cap_nhat_tu_kho(tk_id):
    """Callback ghi trạng thái đường vào kho cho bộ chạy đường dùng."""
    def _ghi(duong, trang_thai, moc):
        _dat(tk_id, duong, trang_thai, moc)
    return _ghi


def _thuc_thi(ket_qua_theo_duong, goi):
    """Dựng bản đồ đường → hàm thực thi giả. Giá trị: chuỗi = thành công;
    ngoại lệ LoiDuong = lỗi có mã/tín hiệu cấu trúc."""
    from app import duong_dung as dd

    def _mot(duong, kq):
        async def _chay():
            goi.append(duong)
            if isinstance(kq, Exception):
                raise kq
            return kq
        return _chay

    return {d: _mot(d, kq) for d, kq in ket_qua_theo_duong.items()}


def _chay_luot(tk_id, ket_qua_theo_duong, goi):
    """Một lượt qua bộ chọn đường của CHÍNH tài khoản (trạng thái đọc từ kho)."""
    from app import duong_dung as dd

    return asyncio.run(dd.chay_qua_tai_khoan(
        _thuc_thi(ket_qua_theo_duong, goi),
        _trang_thai(tk_id),
        cap_nhat=_cap_nhat_tu_kho(tk_id)))


# ── (1) Codex trước web ─────────────────────────────────────────────────────

def test_luot_di_codex_truoc_web(moi_truong):
    tk = _tao_tai_khoan("a232@example.com")
    _dat(tk["id"], "codex", "san_sang")
    goi = []
    kq = _chay_luot(tk["id"], {"codex": "ok-codex", "web": "ok-web"}, goi)
    assert kq.duong == "codex"
    assert kq.gia_tri == "ok-codex"
    assert goi == ["codex"], "đã có Codex thì không được gọi web"


# ── (2) hết hạn mức → chuyển web cùng lượt + ghi trạng thái ────────────────

def test_het_han_muc_chuyen_web_cung_luot(moi_truong):
    from app import duong_dung as dd

    tk = _tao_tai_khoan("b232@example.com")
    _dat(tk["id"], "codex", "san_sang")
    goi = []
    kq = _chay_luot(tk["id"], {
        "codex": dd.LoiDuong(429, {"dat_lai_sau_giay": 600}),
        "web": "ok-web",
    }, goi)
    assert kq.duong == "web"
    assert kq.gia_tri == "ok-web"
    assert goi == ["codex", "web"]
    tt = _trang_thai(tk["id"])
    assert tt["codex"]["trang_thai"] == "het_han_muc"
    bay_gio = int(time.time())
    assert bay_gio + 540 <= tt["codex"]["moc_hoi_lai"] <= bay_gio + 660
    assert tt["web"]["trang_thai"] == "san_sang"


# ── (3) qua mốc hồi → Codex lại ─────────────────────────────────────────────

def test_codex_hoi_lai_sau_moc(moi_truong):
    tk = _tao_tai_khoan("c232@example.com")
    _dat(tk["id"], "codex", "het_han_muc", int(time.time()) - 5)
    tt = _trang_thai(tk["id"])
    assert tt["codex"]["trang_thai"] == "san_sang", \
        "het_han_muc quá mốc phải tự về san_sang khi đọc"
    goi = []
    kq = _chay_luot(tk["id"], {"codex": "ok-codex", "web": "ok-web"}, goi)
    assert kq.duong == "codex"
    assert goi == ["codex"]


# ── (4) 401 = cần đăng nhập lại, không thử lại ──────────────────────────────

def test_loi_401_can_dang_nhap_lai_khong_thu_lai(moi_truong):
    from app import duong_dung as dd

    tk = _tao_tai_khoan("d232@example.com")
    _dat(tk["id"], "codex", "san_sang")
    goi = []
    kq = _chay_luot(tk["id"], {
        "codex": dd.LoiDuong(401),
        "web": "ok-web",
    }, goi)
    assert kq.duong == "web"
    assert _trang_thai(tk["id"])["codex"]["trang_thai"] == "can_dang_nhap_lai"
    # Lượt sau: Codex bị bỏ qua hẳn, không gọi lại.
    goi2 = []
    kq2 = _chay_luot(tk["id"], {"codex": "ok-codex", "web": "ok-web-2"}, goi2)
    assert kq2.duong == "web"
    assert goi2 == ["web"]


# ── (5) cả hai đường hết → sang tài khoản kế; chặn bot không ghi hỏng ───────

def test_ca_hai_duong_het_sang_tai_khoan_ke(moi_truong):
    from app import duong_dung as dd

    a = _tao_tai_khoan("e232@example.com")
    b = _tao_tai_khoan("f232@example.com")
    _dat(a["id"], "codex", "san_sang")
    _dat(b["id"], "codex", "san_sang")
    goi = []
    ds = [
        (a["id"], _thuc_thi({
            "codex": dd.LoiDuong(429, {"dat_lai_sau_giay": 300}),
            "web": dd.LoiDuong(403, {"loai": "chan_bot"}),
        }, goi), _trang_thai(a["id"]), _cap_nhat_tu_kho(a["id"])),
        (b["id"], _thuc_thi({"codex": "ok-b-codex", "web": "ok-b-web"}, goi),
         _trang_thai(b["id"]), _cap_nhat_tu_kho(b["id"])),
    ]
    tk_id, kq = asyncio.run(dd.chay_qua_danh_sach(ds))
    assert tk_id == b["id"]
    assert kq.duong == "codex"
    assert goi == ["codex", "web", "codex"]
    # Chặn bot là lỗi môi trường của lượt, KHÔNG đánh hỏng đường web của A:
    assert _trang_thai(a["id"])["web"]["trang_thai"] == "san_sang"
    assert _trang_thai(a["id"])["codex"]["trang_thai"] == "het_han_muc"


# ── (6) liên kết Codex: điện thoại → cần người → thử lại → thành công ───────

class _ModuleCodexGia:
    """Module OAuth giả theo đúng hợp đồng thiết bị của openaicodex thật:
    thực thể «dang_nhap» với hai hành động bat_dau_dang_nhap / dan_callback."""

    tien_to = "codex-gia"

    def __init__(self, ket_qua_callback):
        self.manifest = {"id": "codex-gia",
                         "nhom": {"id": "chatgpt", "ten": "ChatGPT"}}
        self.ket_qua_callback = ket_qua_callback
        self.goi = []

    async def thuc_the(self, loc=None):
        return [{
            "id": "codex-gia.dang-nhap",
            "ten": "Đăng nhập Codex (OAuth)",
            "loai": "dang_nhap",
            "thuoc_tinh": {},
            "tinh_nang": [
                {"hanh_dong": "bat_dau_dang_nhap", "tham_so": {}},
                {"hanh_dong": "dan_callback", "tham_so": {}},
            ],
            "rui_ro_mac_dinh": "ghi",
        }]

    async def thuc_hien(self, id, hanh_dong, tham_so):
        self.goi.append(hanh_dong)
        if hanh_dong == "bat_dau_dang_nhap":
            return {"ok": True, "trang_thai_moi": {
                "auth_url": "https://auth.example/authorize?state=gia232",
                "state": "gia232"}}
        if hanh_dong == "dan_callback":
            return self.ket_qua_callback
        return {"ok": False, "loi": "không hỗ trợ"}


def _cai_module_codex(so, ket_qua_callback):
    mod = _ModuleCodexGia(ket_qua_callback)
    so.catalog["codex-gia"] = SimpleNamespace(tien_to="codex-gia",
                                               manifest=mod.manifest)
    so.dang_chay["codex-gia"] = mod
    return mod


def test_lien_ket_codex_can_xac_minh_dien_thoai_roi_thu_lai(moi_truong):
    client, so = moi_truong
    tk = _tao_tai_khoan("g232@example.com", dung_cho=("web",))
    assert _trang_thai(tk["id"]).get("codex", {}).get("trang_thai") \
        == "chua_lien_ket"
    mod = _cai_module_codex(so, {
        "ok": False, "ma_loi": "phone_verification_required",
        "loi": "cần xác minh điện thoại trước khi liên kết"})

    r = client.post(f"/api/tai-khoan/da-luu/{tk['id']}/lien-ket-codex",
                    headers=_QT, json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["auth_url"].startswith("https://auth.example/authorize")
    assert mod.goi == ["bat_dau_dang_nhap"]

    r = client.post(
        f"/api/tai-khoan/da-luu/{tk['id']}/lien-ket-codex/hoan-tat",
        headers=_QT, json={"url_callback": ""})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["thanh_cong"] is False
    assert j["can_nguoi"] is True
    tt = _trang_thai(tk["id"])
    assert tt["codex"]["trang_thai"] == "can_xac_minh_dien_thoai"
    assert tt["web"]["trang_thai"] == "san_sang", \
        "đường web phải vẫn dùng được khi Codex chờ xác minh"

    # Người đã xác minh xong → «Thử liên kết Codex lại» mở được OAuth mới.
    mod.ket_qua_callback = {"ok": True, "trang_thai_moi": {
        "email_che": "g***@example.com", "co_refresh_token": True}}
    r = client.post(f"/api/tai-khoan/da-luu/{tk['id']}/thu-lai-codex",
                    headers=_QT, json={})
    assert r.status_code == 200, r.text
    assert r.json()["auth_url"].startswith("https://auth.example/authorize")
    r = client.post(
        f"/api/tai-khoan/da-luu/{tk['id']}/lien-ket-codex/hoan-tat",
        headers=_QT, json={"url_callback":
                           "http://localhost:1455/auth/callback?code=x&state=y"})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    tt = _trang_thai(tk["id"])
    assert tt["codex"]["trang_thai"] == "san_sang"
    from app import kho_tai_khoan as k
    assert "codex" in k.tim_theo_id(tk["id"])["dung_cho"]


def test_lien_ket_email_khong_khop_khong_ghi_trang_thai(moi_truong):
    client, so = moi_truong
    tk = _tao_tai_khoan("h232@example.com", dung_cho=("web",))
    _cai_module_codex(so, {"ok": True, "trang_thai_moi": {
        "email_che": "x***@example.com", "co_refresh_token": True}})
    r = client.post(
        f"/api/tai-khoan/da-luu/{tk['id']}/lien-ket-codex/hoan-tat",
        headers=_QT, json={"url_callback": ""})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is False
    assert _trang_thai(tk["id"])["codex"]["trang_thai"] == "chua_lien_ket"


# ── (7) hiển thị theo từng đường ────────────────────────────────────────────

def test_duong_dung_api_hien_tung_duong(moi_truong):
    client, _so = moi_truong
    tk = _tao_tai_khoan("i232@example.com")
    _dat(tk["id"], "codex", "san_sang")
    r = client.get("/api/tai-khoan/duong-dung", headers=_QT)
    assert r.status_code == 200, r.text
    ds = {m["id"]: m for m in r.json()["tai_khoan"]}
    muc = ds[tk["id"]]
    duong = {d["dich"]: d for d in muc["duong"]}
    assert set(duong) == {"web", "codex"}
    assert duong["codex"]["trang_thai"] == "san_sang"
    assert duong["web"]["trang_thai"] == "san_sang"
    # Chưa có số liệu theo đường → «khong_biet», không bịa:
    assert duong["codex"]["han_muc"] == "khong_biet"
    assert duong["web"]["han_muc"] == "khong_biet"
    assert muc["dang_dung_duong"] == "codex"
    # Codex hết hạn mức → đường đang dùng chuyển sang web:
    _dat(tk["id"], "codex", "het_han_muc", int(time.time()) + 900)
    r = client.get("/api/tai-khoan/duong-dung", headers=_QT)
    muc = {m["id"]: m for m in r.json()["tai_khoan"]}[tk["id"]]
    assert muc["dang_dung_duong"] == "web"
    duong = {d["dich"]: d for d in muc["duong"]}
    assert duong["codex"]["trang_thai"] == "het_han_muc"
    assert duong["codex"]["moc_hoi_lai"] >= int(time.time()) + 800


# ── kho: kiểm đóng trạng thái đường ─────────────────────────────────────────

def test_dat_trang_thai_duong_kiem_dong(moi_truong):
    from app import kho_tai_khoan as k

    tk = _tao_tai_khoan("j232@example.com")
    with pytest.raises(k.LoiKhoTaiKhoan):
        _dat(tk["id"], "codex", "trang_thai_la")
    with pytest.raises(k.LoiKhoTaiKhoan):
        _dat(tk["id"], "dich_la", "san_sang")
    with pytest.raises(k.LoiKhoTaiKhoan):
        _dat(tk["id"], "codex", "het_han_muc")  # thiếu mốc hồi
    with pytest.raises(k.LoiKhoTaiKhoan):
        _dat(tk["id"], "codex", "het_han_muc", "khong_phai_so")
    assert _dat("tk-khong-co-that", "codex", "san_sang") is None


def test_them_dung_cho_va_mac_dinh_trang_thai(moi_truong):
    from app import kho_tai_khoan as k

    tk = _tao_tai_khoan("k232@example.com", dung_cho=("web",))
    tt = _trang_thai(tk["id"])
    assert tt["web"]["trang_thai"] == "san_sang"
    assert tt["codex"]["trang_thai"] == "chua_lien_ket"
    bk = k.them_dung_cho(tk["id"], "codex")
    assert "codex" in bk["dung_cho"]
    # Thêm trùng không nhân đôi:
    bk = k.them_dung_cho(tk["id"], "codex")
    assert bk["dung_cho"].count("codex") == 1
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.them_dung_cho(tk["id"], "gemini")  # không thuộc (chatgpt, web)


# ── (8) không lộ bí mật ─────────────────────────────────────────────────────

def test_khong_lo_bi_mat_trong_phan_hoi(moi_truong):
    client, so = moi_truong
    tk = _tao_tai_khoan("l232@example.com")
    _cai_module_codex(so, {"ok": True, "trang_thai_moi": {
        "email_che": "l***@example.com", "co_refresh_token": True}})
    van_ban = []
    van_ban.append(client.get("/api/tai-khoan/duong-dung", headers=_QT).text)
    van_ban.append(client.get("/api/tai-khoan/da-luu", headers=_QT).text)
    van_ban.append(client.post(
        f"/api/tai-khoan/da-luu/{tk['id']}/lien-ket-codex",
        headers=_QT, json={}).text)
    van_ban.append(client.post(
        f"/api/tai-khoan/da-luu/{tk['id']}/lien-ket-codex/hoan-tat",
        headers=_QT, json={"url_callback": ""}).text)
    tong = "\n".join(van_ban)
    assert _MK not in tong
    assert _TOTP not in tong
    assert "refresh_token" not in tong.replace("co_refresh_token", "")


# ── giao diện: hai đường + nút liên kết ──────────────────────────────────────

def _noi_dung_trang() -> str:
    return (_GOC / "web" / "He-thong-AI.dc.html").read_text(encoding="utf-8")


def test_trang_co_duong_dung_va_nut_lien_ket():
    s = _noi_dung_trang()
    assert "trang_thai_duong" in s, "trang phải đọc trạng thái từng đường"
    assert "duongDs" in s, "mỗi tài khoản phải dựng danh sách đường dùng"
    assert "lien-ket-codex" in s, "thiếu gọi API liên kết Codex"
    assert "thu-lai-codex" in s, "thiếu gọi API thử liên kết lại"
    assert "hoan-tat" in s, "thiếu gọi API hoàn tất liên kết"
    assert "Liên kết" in s
    assert "Thử liên kết" in s


def test_trang_js_hop_le_node_check_nv232(tmp_path):
    s = _noi_dung_trang()
    khoi = [k for k in re.findall(r"<script[^>]*>(.*?)</script>", s, flags=re.S)
            if k.strip()]
    assert khoi
    for i, js in enumerate(khoi):
        tep = tmp_path / f"trang232-{i}.js"
        tep.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(tep)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"script {i}: {r.stderr}"


# ── đột biến sống sót M6/M10/M11: bổ sung test ───────────────────────────────

def test_loi_5xx_chuyen_duong_khong_ghi_trang_thai(moi_truong):
    """5xx = loi_tam_thoi: chuyển đường trong lượt, KHÔNG ghi trạng thái
    (đột biến M6 từng đổi 5xx thành loi_khac mà test không bắt được)."""
    from app import duong_dung as dd

    tk = _tao_tai_khoan("m232@example.com")
    _dat(tk["id"], "codex", "san_sang")
    goi = []
    kq = _chay_luot(tk["id"], {
        "codex": dd.LoiDuong(503),
        "web": "ok-web",
    }, goi)
    assert kq.duong == "web"
    assert kq.gia_tri == "ok-web"
    assert goi == ["codex", "web"]
    tt = _trang_thai(tk["id"])
    assert tt["codex"]["trang_thai"] == "san_sang", \
        "lỗi tạm thời không được đánh hỏng đường"


def test_429_khong_tin_hieu_dung_moc_mac_dinh(moi_truong):
    """429 không kèm dat_lai_sau_giay/moc_hoi_lai → mốc = bây giờ + 3600s
    (đột biến M10 từng đổi 3600 thành 7200 mà test không bắt được)."""
    from app import duong_dung as dd

    tk = _tao_tai_khoan("n232@example.com")
    _dat(tk["id"], "codex", "san_sang")
    goi = []
    kq = _chay_luot(tk["id"], {
        "codex": dd.LoiDuong(429),
        "web": "ok-web",
    }, goi)
    assert kq.duong == "web"
    moc = _trang_thai(tk["id"])["codex"]["moc_hoi_lai"]
    bay_gio = int(time.time())
    assert bay_gio + 3540 <= moc <= bay_gio + 3660


def test_co_xac_minh_dien_thoai_dang_boolean(moi_truong):
    """Kết quả callback mang cờ boolean can_xac_minh_dien_thoai (không có
    ma_loi) → vẫn phân loại can_xac_minh_dien_thoai (đột biến M11 từng bỏ
    nhánh này mà test không bắt được)."""
    from app import duong_dung as dd

    loai, ma_loi, _huong_dan = dd.phan_loai_lien_ket_that_bai(
        {"ok": False, "can_xac_minh_dien_thoai": True})
    assert loai == "can_xac_minh_dien_thoai"
