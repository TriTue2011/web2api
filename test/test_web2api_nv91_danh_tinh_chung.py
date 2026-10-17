"""Kiểm thử hợp đồng «danh tính dùng chung» cho module đăng nhập bằng Gmail/email (NV91-P1).

Mô hình (chốt 17/10 theo c2a + NV92): «danh tính dùng chung» = HỒ SƠ GOOGLE —
một hồ sơ trình duyệt = một Gmail đã đăng nhập sống. Mỗi provider gắn tài khoản
ĐỘC LẬP của nó vào hồ sơ đó; lõi chỉ giữ email + nhãn; cookie/phiên/mật khẩu
nằm trong kho RIÊNG của từng module (quyết định 1.2), nên gỡ/hỏng module này
không ảnh hưởng module kia.

Hợp đồng lõi (không tên nhà cung cấp):
  * module khai
    `dang_nhap_bang` ∈ {"gmail", "email"} trong manifest;
  * `danh_tinh.chua_gan(module_id)` — liệt kê danh tính chưa gắn vào module;
  * `danh_tinh.tao_va_gan(email, nhan, module_id)` — tạo-nếu-chưa-có + gắn
    (lũy đẳng: email đã có thì chỉ gắn, không trùng);
  * `danh_tinh.go_theo_module(module_id)` — gỡ module khỏi mọi danh tính.
API (đều sau xác thực quản trị):
  * GET  /api/danh-tinh/chua-gan?module_id=…
  * POST /api/danh-tinh/tao-va-gan   {email, nhan?, module_id}
  * POST /api/danh-tinh/go-theo-module {module_id}

Máy chủ/dịch vụ GIẢ; không mạng thật.
"""

from __future__ import annotations

import json
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh, khoa_khach, so_module
from app.main import app

KHOA_QT = "khoa-qt-gia-nv91-p1-aaaa"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

MANIFEST_A = {
    "id": "mod-gia-a", "ten": "Module giả A", "mo_ta": "Module giả A cho NV91-P1",
    "loai": "ai", "hang": "A", "giao_thuc": "gia", "tien_to": "modgiaa",
    "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "dang_nhap_bang": "gmail",
    "truong_cau_hinh": [],
}
MANIFEST_B = dict(MANIFEST_A, id="mod-gia-b", ten="Module giả B",
                  mo_ta="Module giả B cho NV91-P1", tien_to="modgiab",
                  dang_nhap_bang="email")

MA_MODULE = textwrap.dedent('''
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
    for mid, manifest in (("mod-gia-a", MANIFEST_A), ("mod-gia-b", MANIFEST_B)):
        thu_muc = tmp_path / "data" / "modules" / mid
        thu_muc.mkdir(parents=True)
        (thu_muc / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (thu_muc / "module.py").write_text(MA_MODULE, encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with TestClient(app) as client:
        assert "mod-gia-a" in client.app.state.so.catalog
        assert "mod-gia-b" in client.app.state.so.catalog
        yield client


# ───────────── tao_va_gan: lũy đẳng, không trùng ─────────────

def test_tao_va_gan_idempotent(moi_truong):
    client = moi_truong
    than = {"email": "Ban@Gmail.com", "nhan": "Gmail cá nhân", "module_id": "mod-gia-a"}
    r = client.post("/api/danh-tinh/tao-va-gan", headers=QT, json=than)
    assert r.status_code == 201, r.text
    dt1 = r.json()["danh_tinh"]
    assert r.json()["da_co_san"] is False
    assert dt1["email"] == "ban@gmail.com"  # chuẩn hoá hoa/thường

    # Email đã có (hoa/thường khác) → chỉ gắn, KHÔNG tạo trùng → 200.
    r = client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                    json={"email": "BAN@gmail.com", "module_id": "mod-gia-a"})
    assert r.status_code == 200, r.text
    assert r.json()["da_co_san"] is True
    assert r.json()["danh_tinh"]["id"] == dt1["id"]

    r = client.get("/api/danh-tinh", headers=QT)
    assert len(r.json()["danh_tinh"]) == 1  # một bản ghi duy nhất


def test_cung_email_hai_module_doc_lap(moi_truong):
    """Cùng một email gắn vào hai module → hai tài khoản độc lập: gỡ một bên
    không mất bên kia; bản ghi lõi chỉ có email+nhãn, không bí mật."""
    client = moi_truong
    r1 = client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                     json={"email": "chung@gmail.com", "module_id": "mod-gia-a"})
    r2 = client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                     json={"email": "chung@gmail.com", "module_id": "mod-gia-b"})
    assert r1.json()["danh_tinh"]["id"] == r2.json()["danh_tinh"]["id"]
    dt = r2.json()["danh_tinh"]
    assert dt["gan"] == ["mod-gia-a", "mod-gia-b"]
    # Bản ghi công khai không bao giờ chứa cookie/mật khẩu (quyết định 1.2).
    # NV92 thêm trạng thái đăng nhập + tên hồ sơ trình duyệt (không phải bí mật).
    assert set(dt) == {"id", "email", "nhan", "ngay_tao", "gan",
                       "trang_thai_dang_nhap", "ho_so_trinh_duyet"}

    # Gỡ module A → danh tính vẫn còn, chỉ mất gắn của A.
    r = client.post("/api/danh-tinh/go-theo-module", headers=QT,
                    json={"module_id": "mod-gia-a"})
    assert r.json() == {"ok": True, "da_go": 1}
    dt = client.get("/api/danh-tinh", headers=QT).json()["danh_tinh"][0]
    assert dt["gan"] == ["mod-gia-b"]


def test_chua_gan_loai_tru_dung_module(moi_truong):
    client = moi_truong
    client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                json={"email": "a@gmail.com", "module_id": "mod-gia-a"})
    client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                json={"email": "b@gmail.com", "module_id": "mod-gia-a"})
    r = client.get("/api/danh-tinh/chua-gan", headers=QT,
                   params={"module_id": "mod-gia-a"})
    assert r.status_code == 200, r.text
    assert r.json()["danh_tinh"] == []  # đã gắn cả hai vào A
    r = client.get("/api/danh-tinh/chua-gan", headers=QT,
                   params={"module_id": "mod-gia-b"})
    assert [d["email"] for d in r.json()["danh_tinh"]] == ["a@gmail.com", "b@gmail.com"]


def test_go_theo_module_don_sach_khong_nem(moi_truong):
    client = moi_truong
    r = client.post("/api/danh-tinh/go-theo-module", headers=QT,
                    json={"module_id": "mod-gia-a"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "da_go": 0}


# ───────────── 401 / 403 / 400 / 404 ─────────────

def test_duong_moi_401_403(moi_truong):
    client = moi_truong
    _, khoa_tho = khoa_khach.tao_khoa("Khoá hẹp NV91", pham_vi=["chat"])
    khach = {"Authorization": f"Bearer {khoa_tho}"}
    # Không khoá → 401.
    assert client.get("/api/danh-tinh/chua-gan",
                      params={"module_id": "mod-gia-a"}).status_code == 401
    assert client.post("/api/danh-tinh/tao-va-gan",
                       json={"email": "x@gmail.com",
                             "module_id": "mod-gia-a"}).status_code == 401
    assert client.post("/api/danh-tinh/go-theo-module",
                       json={"module_id": "mod-gia-a"}).status_code == 401
    # Khoá khách phạm vi hẹp → 403.
    assert client.get("/api/danh-tinh/chua-gan", headers=khach,
                      params={"module_id": "mod-gia-a"}).status_code == 403
    assert client.post("/api/danh-tinh/tao-va-gan", headers=khach,
                       json={"email": "x@gmail.com",
                             "module_id": "mod-gia-a"}).status_code == 403
    assert client.post("/api/danh-tinh/go-theo-module", headers=khach,
                       json={"module_id": "mod-gia-a"}).status_code == 403


def test_duong_moi_400_404(moi_truong):
    client = moi_truong
    # module_id sai khuôn → 400; module chưa cài → 404 (IDOR: không đoán được gì).
    assert client.get("/api/danh-tinh/chua-gan", headers=QT,
                      params={"module_id": "../lan"}).status_code == 400
    assert client.get("/api/danh-tinh/chua-gan", headers=QT,
                      params={"module_id": "khong-co"}).status_code == 404
    r = client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                    json={"email": "x@gmail.com", "module_id": "khong-co"})
    assert r.status_code == 404, r.text
    r = client.post("/api/danh-tinh/tao-va-gan", headers=QT,
                    json={"email": "khong-phai-email", "module_id": "mod-gia-a"})
    assert r.status_code == 400, r.text
    r = client.post("/api/danh-tinh/go-theo-module", headers=QT,
                    json={"module_id": "../lan"})
    assert r.status_code == 400, r.text
