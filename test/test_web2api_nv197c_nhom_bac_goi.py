"""NV197c — NHÓM + BẬC GÓI tài khoản AI (ô #4 của NV197a, gỡ xfail).

Nguyên tắc: nhóm suy từ MODULE sở hữu tài khoản (``manifest.nhom.id``), KHÔNG
khớp chuỗi trên email/token; bậc gói chỉ khi nguồn tin cậy cung cấp (người
dùng nhập tay) — không biết thì ``khong_biet``, KHÔNG đoán.

Chạy qua lõi thật (TestClient), dữ liệu cô lập trong tmp.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

_KHOA_QT = "khoa-qt-nv197c-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}


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


def _cai_module(so, module_id, nhom_id=None, thanh_cong=True):
    """Cài module GIẢ vào lõi: catalog (đã cài) + dang_chay (đang chạy).

    `nhom_id`: id nhóm trong manifest (None = manifest không có mục nhom).
    `thanh_cong`: kết quả dang_nhap giả.
    """
    async def dang_nhap(dong):
        return SimpleNamespace(thanh_cong=thanh_cong, can_nguoi=False, ma_loi="")

    man = {"id": module_id}
    if nhom_id is not None:
        man["nhom"] = {"id": nhom_id, "ten": nhom_id}
    fake = SimpleNamespace(manifest=man, dang_nhap=dang_nhap)
    so.catalog[module_id] = SimpleNamespace(tien_to=module_id, manifest=man)
    so.dang_chay[module_id] = fake


def _them(client, email, **them):
    body = {"email": email, "mat_khau": "MatKhauGia123!"}
    body.update(them)
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=body)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


def _lay(client, **qs):
    r = client.get("/api/tai-khoan/da-luu", headers=_QT, params=qs)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


# ── (a) nhóm suy từ module, không đoán ───────────────────────────────────────

def test_luu_kem_module_id_suy_nhom_tu_manifest(moi_truong):
    """Lưu kèm module_id đã cài → nhom = manifest.nhom.id của module đó."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    bk = _them(client, "a197c-1@example.com", module_id="chatgpt-web")
    assert bk["module_id"] == "chatgpt-web"
    assert bk["nhom"] == "chatgpt"


def test_nhom_khong_doan_tu_email(moi_truong):
    """KHÔNG đoán nhóm từ email: email chứa 'chatgpt' nhưng không gắn module
    → nhom rỗng (không tự suy)."""
    client, _so = moi_truong
    bk = _them(client, "chatgpt-user@example.com")
    assert bk["nhom"] == "", "đoán nhóm từ email là hành vi cấm của NV197c"


def test_module_chua_cai_404(moi_truong):
    """module_id chưa cài → 404 module_chua_cai (không lưu)."""
    client, _so = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"email": "a197c-2@example.com",
                          "mat_khau": "MatKhauGia123!",
                          "module_id": "module-khong-ton-tai"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "module_chua_cai"


def test_module_khong_co_nhom_trong_manifest_dung_module_id(moi_truong):
    """Manifest không khai nhom → nhóm = chính module_id (vẫn suy từ module,
    không đoán từ chỗ khác)."""
    client, so = moi_truong
    _cai_module(so, "module-la", nhom_id=None)
    bk = _them(client, "a197c-3@example.com", module_id="module-la")
    assert bk["nhom"] == "module-la"


def test_dang_nhap_thanh_cong_hoc_module_so_huu(moi_truong):
    """Đăng nhập thành công bằng module → kho học module_id + nhóm."""
    client, so = moi_truong
    _cai_module(so, "gemini-web", nhom_id="google", thanh_cong=True)
    bk = _them(client, "a197c-4@example.com")
    assert bk["nhom"] == ""
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": "gemini-web"})
    assert r.status_code == 200 and r.json()["thanh_cong"]
    sau = _lay(client)
    tk = next(b for b in sau if b["id"] == bk["id"])
    assert tk["module_id"] == "gemini-web"
    assert tk["nhom"] == "google"


def test_dang_nhap_that_khong_hoc_module(moi_truong):
    """Đăng nhập THẤT BẠI → không học module/nhóm (giữ nguyên cũ)."""
    client, so = moi_truong
    _cai_module(so, "grok-web", nhom_id="grok", thanh_cong=False)
    bk = _them(client, "a197c-5@example.com")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": "grok-web"})
    assert r.status_code == 200 and not r.json()["thanh_cong"]
    tk = next(b for b in _lay(client) if b["id"] == bk["id"])
    assert tk["nhom"] == "" and tk["module_id"] == ""


# ── (b) bậc gói: chỉ khi biết, không thì khong_biet ──────────────────────────

def test_bac_goi_mac_dinh_khong_biet(moi_truong):
    """Không khai bậc gói → hiển thị khong_biet, không lỗi."""
    client, _so = moi_truong
    bk = _them(client, "a197c-6@example.com")
    assert bk["bac_goi"] == "khong_biet"


def test_bac_goi_nguoi_dung_khai_va_giu_nguyen_khi_cap_nhat(moi_truong):
    """Người dùng khai bac_goi → lưu; cập nhật không truyền lại → giữ nguyên."""
    client, _so = moi_truong
    bk = _them(client, "a197c-7@example.com", bac_goi="plus")
    assert bk["bac_goi"] == "plus"
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"id": bk["id"], "email": "a197c-7@example.com",
                          "mat_khau": "MatKhauGia123!", "nhan": "doi-nhan"})
    assert r.status_code == 200
    assert r.json()["tai_khoan"]["bac_goi"] == "plus"


def test_bac_goi_khong_phai_chuoi_400_khong_500(moi_truong):
    """Bậc gói lạ (kiểu sai) → 400 invalid_value, không vỡ 500."""
    client, _so = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"email": "a197c-8@example.com",
                          "mat_khau": "MatKhauGia123!", "bac_goi": 123})
    assert r.status_code == 400


# ── (c) bản công khai: đủ trường, không lộ bí mật ────────────────────────────

def test_ban_cong_khai_co_nhom_bac_goi_va_khong_lo_bi_mat(moi_truong):
    """GET trả nhom + bac_goi + module_id; KHÔNG lộ mật khẩu/TOTP/email thô."""
    client, so = moi_truong
    _cai_module(so, "openai-codex", nhom_id="openai")
    _them(client, "a197c-9@example.com", mat_khau="BiMatTuyetDoi99!",
          totp_secret="JBSWY3DPEHPK3PXP", module_id="openai-codex",
          bac_goi="pro")
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    raw = r.text
    assert "BiMatTuyetDoi99!" not in raw
    assert "JBSWY3DPEHPK3PXP" not in raw
    assert "a197c-9@example.com" not in raw  # email thô không lộ
    bk = r.json()["tai_khoan"][0]
    assert bk["nhom"] == "openai"
    assert bk["bac_goi"] == "pro"
    assert bk["module_id"] == "openai-codex"
    assert "mat_khau" not in bk and "totp_secret" not in bk


# ── (d) lọc ?nhom= ───────────────────────────────────────────────────────────

def test_loc_theo_nhom(moi_truong):
    """?nhom= lọc chính xác; thiếu → trả tất cả; lạ → rỗng."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    _cai_module(so, "claude-web", nhom_id="claude")
    _them(client, "a197c-10@example.com", module_id="chatgpt-web")
    _them(client, "a197c-11@example.com", module_id="claude-web")
    _them(client, "a197c-12@example.com")  # chưa nhóm
    chi = _lay(client, nhom="chatgpt")
    assert [b["nhom"] for b in chi] == ["chatgpt"]
    tat = _lay(client)
    assert len(tat) == 3
    la = _lay(client, nhom="nhom-khong-co")
    assert la == []


# ── (e) mức kho (store-level) ────────────────────────────────────────────────

def test_ban_ghi_nhap_nhom_theo_nguon(moi_truong):
    """Bản ghi nhập (loại nhap): nhom = nguồn nhập (đóng); dat_module KHÔNG
    ghi đè nhóm của bản ghi nhập."""
    from app import kho_tai_khoan as k

    kq = k.nhap_nhieu_tai_khoan([{
        "nguon": "sub2api", "dinh_danh": "dd-nhap-197c",
        "bi_mat": "token-gia-nhap-197c", "nhan": "nhap"}])
    assert kq[0]["ket_qua"] == "da_them", kq
    id_nhap = k.ma_tai_khoan_nhap("sub2api", "dd-nhap-197c")
    bk = k.ban_cong_khai(k.tim_theo_id(id_nhap))
    assert bk["nhom"] == "sub2api"
    assert bk["bac_goi"] == "khong_biet"
    # dat_module trên bản ghi nhập: giữ nhóm theo nguồn, không ghi đè —
    # kể cả bản ghi THÔ trong kho (không chỉ bản công khai).
    sau = k.dat_module(id_nhap, "chatgpt-web", "chatgpt")
    assert sau["nhom"] == "sub2api"
    assert sau["module_id"] == ""
    tho = k.tim_theo_id(id_nhap)
    assert tho.get("module_id", "") == ""
    assert tho.get("nhom", "") == ""


def test_dat_module_id_la_tra_none(moi_truong):
    """dat_module với id lạ → None, không ném."""
    from app import kho_tai_khoan as k

    assert k.dat_module("id-khong-co", "chatgpt-web", "chatgpt") is None


def test_xoa_nhom_khi_dat_module_rong(moi_truong):
    """dat_module('') xoá gắn kết (module_id/nhom rỗng) — không kẹt nhóm cũ."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    bk = _them(client, "a197c-13@example.com", module_id="chatgpt-web")
    assert bk["nhom"] == "chatgpt"
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"id": bk["id"], "email": "a197c-13@example.com",
                          "mat_khau": "MatKhauGia123!", "module_id": ""})
    assert r.status_code == 200
    assert r.json()["tai_khoan"]["nhom"] == ""
    assert r.json()["tai_khoan"]["module_id"] == ""
