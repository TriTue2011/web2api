"""NV218 — Kho tài khoản: nhóm → loại → đích dùng + «đang dùng #1» (bước a ĐO, b nhỏ).

Đo: kho đã có nhom/module_id/bac_goi (NV197c) nhưng KHÔNG có «loại»
(api | web | gmail), nhom là chuỗi tự do (không bảng khai báo), Codex là
nhóm riêng «openai» (trái thiết kế chủ máy), «đích dùng» google
(api/gemini/antigravity) chưa có chỗ lưu, «đang dùng #1» chưa tồn tại.

Làm (b): bảng khai báo NHOM_AI / LOAI_TAI_KHOAN_THEO_NHOM /
DICH_DUNG_THEO_NHOM_LOAI (dữ liệu, không rải if); trường loai_tai_khoan +
dich_dung trên bản ghi; API gom theo nhóm→loại + dang_dung; mật khẩu/OTP
không bao giờ ra API.

Chạy qua lõi thật (TestClient), dữ liệu cô lập trong tmp.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

_KHOA_QT = "khoa-qt-nv218-do-0123456789abcdef"
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


def _cai_module(so, module_id, nhom_id=None):
    async def dang_nhap(dong):
        return SimpleNamespace(thanh_cong=True, can_nguoi=False, ma_loi="")

    man = {"id": module_id}
    if nhom_id is not None:
        man["nhom"] = {"id": nhom_id, "ten": nhom_id}
    so.catalog[module_id] = SimpleNamespace(tien_to=module_id, manifest=man)
    so.dang_chay[module_id] = SimpleNamespace(manifest=man, dang_nhap=dang_nhap)


def _them(client, email, **them):
    body = {"email": email, "mat_khau": "MatKhauGia123!"}
    body.update(them)
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=body)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


# ── (a) bảng khai báo loại theo nhóm ─────────────────────────────────────────

def test_loai_theo_nhom_dung_bang(moi_truong):
    """loai_tai_khoan chỉ nhận giá trị trong bảng của đúng nhóm."""
    from app import kho_tai_khoan as k

    assert k.kiem_loai_tai_khoan("web", "chatgpt") == "web"
    assert k.kiem_loai_tai_khoan("api", "chatgpt") == "api"
    assert k.kiem_loai_tai_khoan("gmail", "google") == "gmail"
    assert k.kiem_loai_tai_khoan("api", "google") == "api"
    assert k.kiem_loai_tai_khoan("web", "muse") == "web"
    assert k.kiem_loai_tai_khoan("", "chatgpt") == ""
    assert k.kiem_loai_tai_khoan(None, "chatgpt") == ""
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_loai_tai_khoan("gmail", "chatgpt")  # gmail chỉ của google
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_loai_tai_khoan("api", "muse")  # muse chỉ có web
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_loai_tai_khoan("web", "openai")  # ngoài 5 nhóm AI
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_loai_tai_khoan("web", "")  # chưa có nhóm thì chưa phân loại


def test_dich_dung_theo_bang(moi_truong):
    """dich_dung chỉ nhận giá trị trong bảng của đúng (nhóm, loại)."""
    from app import kho_tai_khoan as k

    assert k.kiem_dich_dung("codex", "chatgpt", "web") == "codex"
    assert k.kiem_dich_dung("web", "chatgpt", "web") == "web"
    assert k.kiem_dich_dung("gemini", "google", "gmail") == "gemini"
    assert k.kiem_dich_dung("antigravity", "google", "gmail") == "antigravity"
    assert k.kiem_dich_dung("api", "google", "api") == "api"
    assert k.kiem_dich_dung("", "chatgpt", "web") == ""
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_dich_dung("codex", "chatgpt", "api")  # codex chỉ của web
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_dich_dung("gemini", "google", "api")  # gemini chỉ của gmail
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_dich_dung("web", "grok", "web")  # grok/web chưa khai báo đích dùng


# ── (b) lưu + liệt kê qua route ──────────────────────────────────────────────

def test_luu_loai_dich_dung_qua_route(moi_truong):
    """POST nhận loai_tai_khoan/dich_dung, GET trả lại đầy đủ."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    bk = _them(client, "a218-1@example.com", module_id="chatgpt-web",
               loai_tai_khoan="web", dich_dung="codex", bac_goi="plus")
    assert bk["nhom"] == "chatgpt"
    assert bk["loai_tai_khoan"] == "web"
    assert bk["dich_dung"] == "codex"
    assert bk["bac_goi"] == "plus"


def test_loai_sai_nhom_400(moi_truong):
    """loai_tai_khoan không thuộc bảng của nhóm → 400."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "a218-2@example.com", "mat_khau": "MatKhauGia123!",
        "module_id": "chatgpt-web", "loai_tai_khoan": "gmail"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_value"


def test_loai_ngoai_nhom_ai_400(moi_truong):
    """Nhóm ngoài 5 nhóm AI thì chưa phân loại → đặt loai là 400."""
    client, so = moi_truong
    _cai_module(so, "khoa-cua", nhom_id="khoa-cua")
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "a218-3@example.com", "mat_khau": "MatKhauGia123!",
        "module_id": "khoa-cua", "loai_tai_khoan": "web"})
    assert r.status_code == 400


def test_ban_ghi_cu_thieu_truong_mac_dinh_rong(moi_truong):
    """Bản ghi cũ (không có trường mới) → loai_tai_khoan/dich_dung = ''."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    bk = _them(client, "a218-4@example.com", module_id="chatgpt-web")
    assert bk["loai_tai_khoan"] == ""
    assert bk["dich_dung"] == ""


def test_khong_lo_bi_mat(moi_truong):
    """ban_cong_khai và API liệt kê KHÔNG bao giờ lộ mật khẩu/TOTP/bi_mat."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    bk = _them(client, "a218-5@example.com", module_id="chatgpt-web",
               loai_tai_khoan="web", totp_secret="JBSWY3DPEHPK3PXP")
    for khoa in ("mat_khau", "totp_secret", "bi_mat"):
        assert khoa not in bk
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200
    for tk in r.json()["tai_khoan"]:
        for khoa in ("mat_khau", "totp_secret", "bi_mat"):
            assert khoa not in tk
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    assert r.status_code == 200
    for g_nhom in r.json()["nhom_loai"].values():
        for g_loai in g_nhom.values():
            for tk in g_loai["tai_khoan"]:
                for khoa in ("mat_khau", "totp_secret", "bi_mat"):
                    assert khoa not in tk


# ── (c) gom theo nhóm → loại + dang_dung ──────────────────────────────────────

def test_gom_theo_nhom_loai(moi_truong):
    """/nhom-loai gom đúng nhóm → loại."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    _cai_module(so, "gemini-web", nhom_id="google")
    _them(client, "a218-6@example.com", module_id="chatgpt-web",
          loai_tai_khoan="web", dich_dung="codex")
    _them(client, "a218-7@example.com", module_id="chatgpt-web",
          loai_tai_khoan="api")
    _them(client, "a218-8@example.com", module_id="gemini-web",
          loai_tai_khoan="gmail", dich_dung="gemini")
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    assert r.status_code == 200
    gom = r.json()["nhom_loai"]
    assert set(gom["chatgpt"]) == {"web", "api"}
    assert set(gom["google"]) == {"gmail"}
    assert len(gom["chatgpt"]["web"]["tai_khoan"]) == 1
    assert gom["chatgpt"]["web"]["tai_khoan"][0]["dich_dung"] == "codex"


def test_dang_dung_bo_qua_tam_dung(moi_truong):
    """dang_dung = tài khoản dùng được đầu tiên; bỏ qua tam_dung."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    a = _them(client, "a218-9@example.com", module_id="chatgpt-web",
              loai_tai_khoan="web")
    b = _them(client, "a218-10@example.com", module_id="chatgpt-web",
              loai_tai_khoan="web")
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    gom = r.json()["nhom_loai"]["chatgpt"]["web"]
    assert gom["dang_dung"] == a["id"]  # đầu danh sách, cả hai đều dùng được
    # Tạm dừng A → dang_dung chuyển sang B.
    r = client.post(f"/api/tai-khoan/da-luu/{a['id']}/tam-dung", headers=_QT,
                    json={"tam_dung": True})
    assert r.status_code == 200, r.text
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    gom = r.json()["nhom_loai"]["chatgpt"]["web"]
    assert gom["dang_dung"] == b["id"]
    flags = {tk["id"]: tk["dang_dung"] for tk in gom["tai_khoan"]}
    assert flags == {a["id"]: False, b["id"]: True}


def test_dang_dung_bo_qua_het_han_muc(moi_truong):
    """Tài khoản đang hết hạn mức không được chọn làm dang_dung."""
    import time
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    a = _them(client, "a218-11@example.com", module_id="chatgpt-web",
              loai_tai_khoan="web")
    b = _them(client, "a218-12@example.com", module_id="chatgpt-web",
              loai_tai_khoan="web")
    from app import luong_dung_tai_khoan as ldtk
    assert ldtk.danh_dau_het_han_muc(a["id"], int(time.time()) + 3600)
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    gom = r.json()["nhom_loai"]["chatgpt"]["web"]
    assert gom["dang_dung"] == b["id"]
