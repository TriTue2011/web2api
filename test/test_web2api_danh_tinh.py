"""Kiểm thử kho danh tính + API danh tính (NV67 nhóm 1, việc 1–2 — NV72).

Kho danh tính của lõi: mỗi email nhập MỘT lần; chuẩn hoá hoa/thường; chặn trùng;
xoá bị chặn (409) khi còn tài khoản provider trỏ tới; lưu mã hoá AES-256-GCM quyền 0600.
API: chỉ quản trị — 401 khi không khoá, 403 khi khoá khách phạm vi hẹp, id lạ → 404
không lộ (IDOR).
"""

from __future__ import annotations

import base64
import json
import secrets
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, kho, khoa_khach, so_module
from app.main import app

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

MANIFEST_PROVIDER = {
    "id": "provider-gia", "ten": "Provider giả", "mo_ta": "Module provider giả cho test danh tính",
    "loai": "ai", "hang": "A", "giao_thuc": "gia", "tien_to": "providergia",
    "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "tab_web": {"nhan": "Giả", "trang": "web/index.html"},
    "truong_cau_hinh": [],
}

MA_PROVIDER = textwrap.dedent('''
    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
        def nap(self, cau_hinh):
            pass
        async def cai(self, cau_hinh):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
        async def models(self):
            return []
        async def suc_khoe(self):
            return {"ok": True}
        async def dung(self):
            pass
''')


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    thu_muc = data / "modules" / "provider-gia"
    thu_muc.mkdir(parents=True)
    (thu_muc / "manifest.json").write_text(json.dumps(MANIFEST_PROVIDER, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_PROVIDER, encoding="utf-8")
    (thu_muc / "web").mkdir()
    (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with TestClient(app) as client:
        assert "provider-gia" in client.app.state.so.catalog, client.app.state.so.loi
        yield client, data


def _tao(client, email="ban@gmail.com", nhan="Gmail cá nhân"):
    r = client.post("/api/danh-tinh", headers=QT, json={"email": email, "nhan": nhan})
    assert r.status_code == 201, r.text
    return r.json()["danh_tinh"]


# ───────────── tạo / đọc ─────────────

def test_tao_va_doc(moi_truong):
    """Tạo danh tính → 201, bản ghi đủ 5 trường, id ổn định dạng dt-<16hex>."""
    client, _ = moi_truong
    dt = _tao(client)
    assert dt["id"].startswith("dt-") and len(dt["id"]) == 19
    assert dt["email"] == "ban@gmail.com"
    assert dt["nhan"] == "Gmail cá nhân"
    assert dt["ngay_tao"]
    assert dt["gan"] == []

    r = client.get(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 200
    assert r.json()["danh_tinh"] == dt


def test_id_on_dinh_theo_email(moi_truong):
    """Cùng email luôn cùng id — xoá rồi tạo lại vẫn id cũ (ổn định)."""
    client, _ = moi_truong
    dt1 = _tao(client, email="on-dinh@example.com")
    client.delete(f"/api/danh-tinh/{dt1['id']}", headers=QT)
    dt2 = _tao(client, email="on-dinh@example.com")
    assert dt1["id"] == dt2["id"]


def test_chuan_hoa_chu_hoa_thuong(moi_truong):
    """«Ban@Gmail.COM» lưu thành «ban@gmail.com» — hoa/thường coi như một."""
    client, _ = moi_truong
    dt = _tao(client, email="  Ban@Gmail.COM  ")
    assert dt["email"] == "ban@gmail.com"


def test_trung_email_bi_chan(moi_truong):
    """Tạo trùng email (kể cả khác hoa/thường) → 400 email_ton_tai, không tạo bản ghi thứ hai."""
    client, _ = moi_truong
    _tao(client, email="trung@example.com")
    r = client.post("/api/danh-tinh", headers=QT, json={"email": "TRUNG@Example.COM"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "email_ton_tai"
    r = client.get("/api/danh-tinh", headers=QT)
    assert sum(1 for d in r.json()["danh_tinh"] if d["email"] == "trung@example.com") == 1


def test_email_sai_khuon(moi_truong):
    """Thiếu @, có khoảng trắng, miền không dấu chấm, quá dài, không phải chuỗi → 400."""
    client, _ = moi_truong
    for xau in ["khong-co-a", "a @b.com", "a@b", "a" * 250 + "@x.com", 123, None]:
        r = client.post("/api/danh-tinh", headers=QT, json={"email": xau})
        assert r.status_code == 400, xau
    r = client.post("/api/danh-tinh", headers=QT, json={})
    assert r.status_code == 400


def test_nhan_sai(moi_truong):
    """Nhãn quá dài hoặc chứa ký tự điều khiển → 400; nhãn trống → lưu ""."""
    client, _ = moi_truong
    r = client.post("/api/danh-tinh", headers=QT, json={"email": "a1@example.com", "nhan": "x" * 101})
    assert r.status_code == 400
    r = client.post("/api/danh-tinh", headers=QT, json={"email": "a2@example.com", "nhan": "dòng\nmới"})
    assert r.status_code == 400
    dt = _tao(client, email="a3@example.com", nhan=None)
    assert dt["nhan"] == ""


def test_danh_sach(moi_truong):
    """GET liệt kê đúng các danh tính đã tạo."""
    client, _ = moi_truong
    _tao(client, email="mot@example.com")
    _tao(client, email="hai@example.com")
    r = client.get("/api/danh-tinh", headers=QT)
    assert r.status_code == 200
    emails = {d["email"] for d in r.json()["danh_tinh"]}
    assert {"mot@example.com", "hai@example.com"} <= emails


# ───────────── gắn / gỡ provider ─────────────

def test_gan_va_go_provider(moi_truong):
    """Gắn → 200 và có trong «gan»; gắn lại lũy đẳng không trùng; gỡ → 200; gỡ lại → 404."""
    client, _ = moi_truong
    dt = _tao(client)
    r = client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={"module_id": "provider-gia"})
    assert r.status_code == 200
    assert r.json()["danh_tinh"]["gan"] == ["provider-gia"]

    r = client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={"module_id": "provider-gia"})
    assert r.status_code == 200
    assert r.json()["danh_tinh"]["gan"] == ["provider-gia"], "gắn lại không được trùng"

    r = client.post(f"/api/danh-tinh/{dt['id']}/go", headers=QT, json={"module_id": "provider-gia"})
    assert r.status_code == 200
    assert r.json()["danh_tinh"]["gan"] == []

    r = client.post(f"/api/danh-tinh/{dt['id']}/go", headers=QT, json={"module_id": "provider-gia"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "khong_gan"


def test_mot_danh_tinh_gan_nhieu_provider(moi_truong):
    """Quyết định chủ kho #1: một danh tính gắn được nhiều provider."""
    client, _ = moi_truong
    dt = _tao(client)
    # Thêm provider thứ hai vào catalog của test
    client.app.state.so.tu_xa["provider-hai"] = {
        "id": "provider-hai", "ten": "Hai", "tien_to": "providerhai", "base_url": "http://127.0.0.1:9",
    }
    for mid in ("provider-gia", "provider-hai"):
        r = client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={"module_id": mid})
        assert r.status_code == 200, r.text
    r = client.get(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.json()["danh_tinh"]["gan"] == ["provider-gia", "provider-hai"]


def test_gan_module_id_sai(moi_truong):
    """module_id sai khuôn → 400; đúng khuôn nhưng chưa cài → 404 module_chua_cai."""
    client, _ = moi_truong
    dt = _tao(client)
    r = client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={"module_id": "Khong Hop Le!"})
    assert r.status_code == 400
    r = client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={"module_id": "chua-cai"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "module_chua_cai"
    r = client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={})
    assert r.status_code == 400


# ───────────── xoá ─────────────

def test_xoa_bi_chan_khi_con_tai_khoan(moi_truong):
    """Xoá danh tính còn tài khoản provider trỏ tới → 409 con_tai_khoan kèm tên module;
    gỡ hết rồi xoá → ok."""
    client, _ = moi_truong
    dt = _tao(client)
    client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=QT, json={"module_id": "provider-gia"})
    r = client.delete(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "con_tai_khoan"
    assert "provider-gia" in body["message"]

    client.post(f"/api/danh-tinh/{dt['id']}/go", headers=QT, json={"module_id": "provider-gia"})
    r = client.delete(f"/api/danh-tinh/{dt['id']}", headers=QT)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert client.get(f"/api/danh-tinh/{dt['id']}", headers=QT).status_code == 404


def test_xoa_id_la_404(moi_truong):
    """Xoá id không tồn tại → 404, không lộ gì."""
    client, _ = moi_truong
    r = client.delete("/api/danh-tinh/dt-khongton-tai0000", headers=QT)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "danh_tinh_khong_co"


# ───────────── 401 / 403 / IDOR ─────────────

def test_khong_khoa_401(moi_truong):
    """Không khoá → 401 trên mọi đường danh tính."""
    client, _ = moi_truong
    dt = _tao(client)
    assert client.get("/api/danh-tinh").status_code == 401
    assert client.post("/api/danh-tinh", json={"email": "x@example.com"}).status_code == 401
    assert client.get(f"/api/danh-tinh/{dt['id']}").status_code == 401
    assert client.delete(f"/api/danh-tinh/{dt['id']}").status_code == 401
    assert client.post(f"/api/danh-tinh/{dt['id']}/gan", json={"module_id": "provider-gia"}).status_code == 401
    assert client.post(f"/api/danh-tinh/{dt['id']}/go", json={"module_id": "provider-gia"}).status_code == 401


def test_khoa_khach_403(moi_truong):
    """Khoá khách phạm vi hẹp gọi đường quản trị → 403."""
    client, _ = moi_truong
    dt = _tao(client)
    _, khoa_tho = khoa_khach.tao_khoa("Khoá hẹp", pham_vi=["chat"])
    khach = {"Authorization": f"Bearer {khoa_tho}"}
    assert client.get("/api/danh-tinh", headers=khach).status_code == 403
    assert client.post("/api/danh-tinh", headers=khach, json={"email": "x@example.com"}).status_code == 403
    assert client.get(f"/api/danh-tinh/{dt['id']}", headers=khach).status_code == 403
    assert client.delete(f"/api/danh-tinh/{dt['id']}", headers=khach).status_code == 403
    assert client.post(f"/api/danh-tinh/{dt['id']}/gan", headers=khach,
                       json={"module_id": "provider-gia"}).status_code == 403
    assert client.post(f"/api/danh-tinh/{dt['id']}/go", headers=khach,
                       json={"module_id": "provider-gia"}).status_code == 403


def test_idor_id_la_404_khong_lo(moi_truong):
    """Đoán id lạ → 404 đồng nhất, không lộ email/nhãn của bản ghi khác."""
    client, _ = moi_truong
    _tao(client, email="bi-mat@example.com")
    id_la = "dt-" + "0" * 16
    for method, duong, than in [
        ("get", f"/api/danh-tinh/{id_la}", None),
        ("delete", f"/api/danh-tinh/{id_la}", None),
        ("post", f"/api/danh-tinh/{id_la}/gan", {"module_id": "provider-gia"}),
        ("post", f"/api/danh-tinh/{id_la}/go", {"module_id": "provider-gia"}),
    ]:
        r = getattr(client, method)(duong, headers=QT, **({"json": than} if than else {}))
        assert r.status_code == 404, (method, duong)
        assert "bi-mat@example.com" not in r.text, "lộ email bản ghi khác"


# ───────────── kho trên đĩa ─────────────

def test_kho_ma_hoa_aes256gcm_quyen_0600(moi_truong, monkeypatch):
    """Có WEB2API_KHOA_CHINH → tệp kho danh tính mã hoá AES-256-GCM (đầu W2A1), quyền 0600."""
    khoa = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("WEB2API_KHOA_CHINH", khoa)
    dt = danh_tinh.tao("mahoa@example.com", "Mã hoá")
    assert dt["email"] == "mahoa@example.com"
    p = kho.thu_muc_du_lieu() / "bi_mat" / "danh_tinh.json"
    raw = p.read_bytes()
    assert raw.startswith(b"W2A1"), "tệp kho danh tính phải mã hoá AES-256-GCM khi có khoá chính"
    assert (p.stat().st_mode & 0o777) == 0o600
    assert b"mahoa@example.com" not in raw, "email lộ dạng rõ trong tệp mã hoá"
    # Đọc lại trong suốt
    assert danh_tinh.lay(dt["id"])["email"] == "mahoa@example.com"


def test_kho_khong_khoa_van_0600(moi_truong):
    """Không có khoá chính → tệp dạng rõ nhưng vẫn quyền 0600, thư mục bi_mat 0700."""
    _tao(moi_truong[0], email="ro@example.com")
    p = kho.thu_muc_du_lieu() / "bi_mat" / "danh_tinh.json"
    assert p.is_file()
    assert (p.stat().st_mode & 0o777) == 0o600
    assert ((p.parent.stat().st_mode) & 0o777) == 0o700


def test_kho_hong_503(moi_truong):
    """Tệp kho hỏng → 503 identity_store_unreadable (đóng khi lỗi, không coi là rỗng)."""
    client, data = moi_truong
    p = data / "bi_mat" / "danh_tinh.json"
    _tao(client, email="truoc@example.com")
    p.write_bytes(b"{hong")
    r = client.get("/api/danh-tinh", headers=QT)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "identity_store_unreadable"
    # Không tự ghi đè: tệp hỏng vẫn còn nguyên
    assert p.read_bytes() == b"{hong"


def test_loi_trung_khong_chep_nguyen_van_email(moi_truong):
    """Lỗi trùng email không chép nguyên văn email khách gửi (chống phản chiếu chuỗi dài)."""
    client, _ = moi_truong
    _tao(client, email="trung2@example.com")
    r = client.post("/api/danh-tinh", headers=QT, json={"email": "  TRUNG2@Example.COM  "})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "email_ton_tai"
    assert "TRUNG2@Example.COM" not in r.json()["error"]["message"]
