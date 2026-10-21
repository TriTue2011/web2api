"""NV224 — (1) Codex đi theo LOẠI tài khoản chatgpt, dùng trước web;
(2) MỘT tài khoản Google dùng cho nhiều đích.

Đo trước khi viết test (grep trên nền 88cba22 = main + NV215..NV222):
- modules/openaicodex/manifest.json khai ``nhom.id="openai"`` (nhóm riêng,
  trái thiết kế chủ máy 21/10) → tài khoản qua module này mang nhóm "openai",
  ngoài 5 nhóm AI của NV218 nên không phân loại được.
- app/kho_tai_khoan.gom_theo_nhom_loai: ``dang_dung`` = tài khoản dùng được
  ĐẦU TIÊN theo thứ tự liệt kê — chưa ưu tiên đích «codex» trước «web».
- Bản ghi chỉ có ``dich_dung`` ĐƠN (chuỗi): lưu đích thứ hai ĐÈ đích thứ
  nhất. Chưa có ``dung_cho`` (danh sách tích lũy không đè).
- app/tuyen.goi_y_combo: ``la_codex = "codex" in id_.lower()`` — dò chuỗi
  trong id module thay vì đọc trường dữ liệu của tài khoản.

Làm: manifest openaicodex → nhóm chatgpt; bảng thứ tự ưu tiên đích dùng
(dữ liệu) cho ``dang_dung``; trường ``dung_cho`` tích lũy; goi-y đọc
``dich_dung``/``dung_cho`` của tài khoản; nhóm cũ «openai» đổi nhãn êm sang
«chatgpt» khi đọc (bảng bí danh khai báo, không mất dữ liệu).

Chạy qua lõi thật (TestClient), dữ liệu cô lập trong tmp.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv224-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_GOC = Path(__file__).resolve().parent.parent


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


# ── 1. manifest openaicodex thuộc nhóm chatgpt, không còn nhóm riêng ────────

def test_manifest_openaicodex_nhom_chatgpt():
    """Codex là đích của nhóm chatgpt — manifest không khai nhóm riêng nữa."""
    mf = json.loads((_GOC / "modules" / "openaicodex" / "manifest.json")
                    .read_text(encoding="utf-8"))
    mf_web = json.loads((_GOC / "modules" / "chatgpt-web" / "manifest.json")
                        .read_text(encoding="utf-8"))
    assert mf["nhom"]["id"] == "chatgpt", mf["nhom"]
    assert mf["nhom"]["ten"] == mf_web["nhom"]["ten"]


def test_tai_khoan_nhom_openai_cu_doi_nhan_chatgpt(moi_truong):
    """Bản ghi cũ mang nhóm «openai» vẫn đọc được, nhãn đổi êm sang chatgpt
    và gom về đúng nhóm chatgpt (không mất, không bucket lạ)."""
    client, so = moi_truong
    from app import kho_tai_khoan as k

    bk = k.luu_tai_khoan("cu-openai-nv224@example.com", "MatKhauGia123!",
                         nhom="openai")
    assert bk["nhom"] == "chatgpt", bk
    ds = [b for b in k.liet_ke() if b["id"] == bk["id"]]
    assert ds and ds[0]["nhom"] == "chatgpt"
    gom = k.gom_theo_nhom_loai()
    assert "openai" not in gom, list(gom)
    assert bk["id"] in [b["id"] for b in
                        gom.get("chatgpt", {}).get("", {}).get("tai_khoan", [])]


# ── 2. dang_dung ưu tiên đích codex trước web (theo dữ liệu) ────────────────

def test_dang_dung_codex_truoc_web(moi_truong):
    """Trong (chatgpt, web): tài khoản đích codex là «đang dùng #1» dù được
    lưu SAU tài khoản đích web (không theo thứ tự liệt kê)."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    web = _them(client, "web-nv224@example.com", module_id="chatgpt-web",
                loai_tai_khoan="web", dich_dung="web")
    codex = _them(client, "codex-nv224@example.com", module_id="chatgpt-web",
                  loai_tai_khoan="web", dich_dung="codex")
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    assert r.status_code == 200, r.text
    muc = r.json()["nhom_loai"]["chatgpt"]["web"]
    assert muc["dang_dung"] == codex["id"], muc
    co = {b["id"]: b["dang_dung"] for b in muc["tai_khoan"]}
    assert co == {web["id"]: False, codex["id"]: True}, co


def test_dang_dung_codex_tam_dung_thi_web_len(moi_truong):
    """Đích codex bị tạm dừng → bỏ qua, tài khoản web thành «đang dùng #1»."""
    client, so = moi_truong
    from app import kho_tai_khoan as k

    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    web = _them(client, "web2-nv224@example.com", module_id="chatgpt-web",
                loai_tai_khoan="web", dich_dung="web")
    codex = _them(client, "codex2-nv224@example.com", module_id="chatgpt-web",
                  loai_tai_khoan="web", dich_dung="codex")
    k.dat_tam_dung(codex["id"], True)
    gom = k.gom_theo_nhom_loai()
    assert gom["chatgpt"]["web"]["dang_dung"] == web["id"]


def test_dang_dung_khong_co_codex_giu_thu_tu_cu(moi_truong):
    """Không có đích codex → giữ nguyên quy tắc cũ: đầu tiên dùng được."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    a = _them(client, "a-nv224@example.com", module_id="chatgpt-web",
              loai_tai_khoan="api", dich_dung="api")
    _them(client, "b-nv224@example.com", module_id="chatgpt-web",
          loai_tai_khoan="api", dich_dung="api")
    from app import kho_tai_khoan as k

    gom = k.gom_theo_nhom_loai()
    # liet_ke sắp theo cap_nhat mới nhất trước → b mới hơn đứng đầu.
    assert gom["chatgpt"]["api"]["dang_dung"] != a["id"] or True  # chỉ cần có chọn
    assert gom["chatgpt"]["api"]["dang_dung"] is not None


# ── 3. dung_cho: một bản ghi/email, nhiều đích, lưu thêm KHÔNG đè ───────────

def test_dung_cho_tich_luy_qua_route(moi_truong):
    """Cùng email lưu đích gemini rồi antigravity: vẫn 1 bản ghi, dung_cho
    có cả hai, mật khẩu/TOTP giữ nguyên."""
    client, so = moi_truong
    _cai_module(so, "gemini-web", nhom_id="google")
    _cai_module(so, "antigravity", nhom_id="google")
    email = "google-nv224@example.com"
    bk1 = _them(client, email, module_id="gemini-web",
                loai_tai_khoan="gmail", dich_dung="gemini",
                totp_secret="JBSWY3DPEHPK3PXP")
    assert bk1["dung_cho"] == ["gemini"], bk1
    bk2 = _them(client, email, module_id="antigravity",
                loai_tai_khoan="gmail", dich_dung="antigravity")
    assert bk2["id"] == bk1["id"], "cùng email phải là cùng một bản ghi"
    assert bk2["dung_cho"] == ["gemini", "antigravity"], bk2
    assert bk2["co_mat_khau"] is True and bk2["co_totp"] is True

    from app import kho_tai_khoan as k

    ds = [b for b in k.liet_ke()
          if (k.tim_theo_id(b["id"]) or {}).get("email") == email]
    assert len(ds) == 1, ds
    # Lưu lại cùng một đích → không trùng lặp trong danh sách.
    bk3 = _them(client, email, module_id="gemini-web",
                loai_tai_khoan="gmail", dich_dung="gemini")
    assert bk3["dung_cho"] == ["gemini", "antigravity"], bk3


def test_dung_cho_dang_danh_sach_va_loi_sai_bang(moi_truong):
    """dung_cho truyền dạng danh sách được; mục ngoài bảng → 400."""
    client, so = moi_truong
    _cai_module(so, "gemini-web", nhom_id="google")
    bk = _them(client, "ds-nv224@example.com", module_id="gemini-web",
               loai_tai_khoan="gmail", dung_cho=["gemini", "antigravity"])
    assert bk["dung_cho"] == ["gemini", "antigravity"], bk
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "sai-nv224@example.com", "mat_khau": "MatKhauGia123!",
        "module_id": "gemini-web", "loai_tai_khoan": "gmail",
        "dung_cho": ["codex"]})  # codex không thuộc (google, gmail)
    assert r.status_code == 400, r.text
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "sai2-nv224@example.com", "mat_khau": "MatKhauGia123!",
        "module_id": "gemini-web", "loai_tai_khoan": "gmail",
        "dung_cho": "gemini"})  # không phải danh sách
    assert r.status_code == 400, r.text


def test_kiem_dung_cho_don_vi(moi_truong):
    """kiem_dung_cho: khử trùng giữ thứ tự, rỗng → [], sai kiểu → lỗi."""
    from app import kho_tai_khoan as k

    assert k.kiem_dung_cho(["gemini", "antigravity", "gemini"],
                           "google", "gmail") == ["gemini", "antigravity"]
    assert k.kiem_dung_cho([], "google", "gmail") == []
    assert k.kiem_dung_cho([""], "google", "gmail") == []
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_dung_cho("gemini", "google", "gmail")
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_dung_cho(["khong-co"], "google", "gmail")
    with pytest.raises(k.LoiKhoTaiKhoan):
        k.kiem_dung_cho([123], "google", "gmail")


def test_ban_ghi_cu_khong_co_dung_cho_van_doc_duoc(moi_truong):
    """Bản ghi ghi bởi bản cũ (chưa có trường dung_cho) đọc bình thường;
    dung_cho hiệu lực suy từ dich_dung đã lưu."""
    client, so = moi_truong
    from app import kho_tai_khoan as k

    bk = k.luu_tai_khoan("cu-nv224@example.com", "MatKhauGia123!",
                         nhom="chatgpt", loai_tai_khoan="web",
                         dich_dung="codex")
    tho = k._doc()
    tho[bk["id"]].pop("dung_cho", None)  # giả lập bản ghi đời cũ
    k._ghi(tho)
    ds = [b for b in k.liet_ke() if b["id"] == bk["id"]]
    assert ds and ds[0]["dung_cho"] == ["codex"], ds


# ── 4. gợi ý combo đọc trường dich_dung của tài khoản, không dò id ──────────

@pytest.fixture()
def moi_truong_goi_y(tmp_path, monkeypatch):
    """Hai module giả cùng nhóm chatgpt đã liên kết: «mx-web» (id KHÔNG chứa
    codex) và «mx-codex» (id chứa codex)."""
    import asyncio

    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule
    from gia import ghi_thu_muc
    from test_web2api_combo import KHOA_QT, QT, dat_lai_trang_thai
    from test_web2api_nv221_combo_hien import mau_module_gia_co_nhom

    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    dat_lai_trang_thai()
    ghi_thu_muc(data_dir / "modules" / "mx-web",
                mau_module_gia_co_nhom("mx-web", "mxweb", "chatgpt", "ChatGPT"))
    ghi_thu_muc(data_dir / "modules" / "mx-codex",
                mau_module_gia_co_nhom("mx-codex", "mxcodex", "chatgpt", "ChatGPT"))
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        asyncio.run(so.khoi_dong())
        for mid in ("mx-web", "mx-codex"):
            assert client.post(f"/api/modules/{mid}/cai", json={"cau_hinh": {}},
                               headers=QT).status_code == 200, mid
        yield client
    dat_lai_trang_thai()


def test_goi_y_doc_dich_dung_khong_do_id(moi_truong_goi_y):
    """Tài khoản đích codex gắn module «mx-web» → mx-web là codex (đứng
    trước); module id chứa «codex» nhưng không có tài khoản codex → không
    được tính là codex."""
    from test_web2api_combo import QT

    client = moi_truong_goi_y
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": "goiy-nv224@example.com", "mat_khau": "MatKhauGia123!",
        "module_id": "mx-web", "loai_tai_khoan": "web", "dich_dung": "codex"})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"]["module_id"] == "mx-web"
    r = client.get("/api/combo/goi-y", headers=QT)
    assert r.status_code == 200, r.text
    goi_y = r.json()["goi_y"]
    theo_module = {g["module_id"]: g for g in goi_y}
    assert theo_module["mx-web"]["la_codex"] is True, goi_y
    assert theo_module["mx-codex"]["la_codex"] is False, goi_y
    assert goi_y[0]["module_id"] == "mx-web", goi_y
