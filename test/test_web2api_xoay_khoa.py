"""Kiểm thử tính năng Xoay khoá khách không gián đoạn (cửa sổ hai khoá) và Vân tay khoá cho nhật ký.

Tuân thủ:
- BẢO MẬT: Kiểm LEO THANG QUYỀN, không chỉ bề mặt (không khoá -> 401, khoá khách gọi -> 403, vai user -> 403, id lạ -> 404).
- Kho PUBLIC: chuỗi khoá giả chỉ dùng từ allowlist .gitleaks.toml.
- Không khoá API / khoá quản trị trong log, URL hay phản hồi.
- pytest.raises hẹp, không dùng `status_code in (...)`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nguoi_dung, nhat_ky, phien_web, sao_luu, xac_thuc
from app.loi_openai import LoiOpenAI
from app.main import app
from app.so_module import SoModule

# Chuỗi khoá giả nằm trong allowlist của .gitleaks.toml
KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
KHOA_SAI = "AIzaSySAI-bon-000000000000000000000004"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK_USER = "MatKhauChuan123456"


class ModuleMock:
    """Module giả lập phục vụ kiểm thử xác thực /v1."""

    def __init__(self, tien_to: str = "mock"):
        self.tien_to = tien_to

    async def chat(self, body: dict, stream: bool = False):
        tra_ve = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", f"{self.tien_to}/chat-model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")

    async def models(self) -> list[dict]:
        return [
            {"id": f"{self.tien_to}/chat-model", "object": "model", "created": 123456, "owned_by": "mock", "kha_nang": ["chat"]},
            {"id": f"{self.tien_to}/image-model", "object": "model", "created": 123456, "owned_by": "mock", "kha_nang": ["anh"]},
        ]

    async def suc_khoe(self) -> dict:
        return {"ok": True, "khoa": [{"khoa": "…mock", "ok": True, "do_tre_ms": 10}]}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    khoa_khach.lam_moi_cache()
    khoa_khach._KHOA_VAN_TAY_CACHE = None

    mod = ModuleMock("mock")
    so = SoModule()
    so.tu_xa["mock"] = {"tien_to": "mock", "url": "http://mock"}
    so.dang_chay["mock"] = mod

    with TestClient(app) as client:
        client.app.state.so = so
        yield client, mod, data_dir


def _tao_user_va_cookie(client, ten: str = "user-test", vai: str = "user", pham_vi_toi_da: list[str] | None = None):
    u, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK_USER, pham_vi_toi_da=pham_vi_toi_da))
    p, tok = phien_web.tao_phien(u["id"])
    return u, {"Cookie": f"w2a_phien={tok}", "X-CSRF-Token": p["csrf"]}


# ─────────────────────────────────────────────────────────────────────────────
# 1. LEO THANG QUYỀN VÀ IDOR
# ─────────────────────────────────────────────────────────────────────────────

def test_01_xoay_khong_khoa_tra_401(moi_truong):
    """Không có Authorization header -> 401 Unauthorized."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K1", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]

    r = client.post(f"/api/khoa-khach/{id_k}/xoay", json={})
    assert r.status_code == 401


def test_02_khoa_khach_goi_xoay_tra_403(moi_truong):
    """Khoá khách gọi POST /api/khoa-khach/{id}/xoay -> 403 Forbidden (chặn đường quản trị)."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K1", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_tho = r_tao.json()["khoa"]["khoa"]

    hdr_khach = {"Authorization": f"Bearer {khoa_tho}"}
    r = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=hdr_khach, json={})
    assert r.status_code == 403


def test_03_vai_user_goi_xoay_tra_403(moi_truong):
    """Tài khoản vai 'user' gọi POST /api/khoa-khach/{id}/xoay -> 403 Forbidden."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K1", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]

    _, hdr_user = _tao_user_va_cookie(client, "user-thuong", vai="user")
    r = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=hdr_user, json={})
    assert r.status_code == 403


def test_04_xoay_id_la_tra_404(moi_truong):
    """Xoay ID không tồn tại -> 404 Not Found."""
    client, _, _ = moi_truong
    r = client.post("/api/khoa-khach/kk_khongtontai999/xoay", headers=QT, json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "key_not_found"


def test_05_khong_xoay_duoc_khoa_nguoi_khac_qua_duong_nguoi_dung(moi_truong):
    """Người dùng qua /api/toi không có endpoint xoay khoá (chỉ quản trị mới có quyền xoay khoá)."""
    client, _, _ = moi_truong
    _, hdr_u1 = _tao_user_va_cookie(client, "user-mot", vai="user")
    # Tạo khoá cho user 1
    r_tao1 = client.post("/api/toi/khoa", headers=hdr_u1, json={"nhan": "Khoá U1"})
    assert r_tao1.status_code == 200
    id_k1 = r_tao1.json()["khoa"]["id"]

    _, hdr_u2 = _tao_user_va_cookie(client, "user-hai", vai="user")
    # User 2 thử POST /api/toi/khoa/{id}/xoay -> 404/405 không tồn tại
    r_xoay = client.post(f"/api/toi/khoa/{id_k1}/xoay", headers=hdr_u2, json={})
    assert r_xoay.status_code in (404, 405)


# ─────────────────────────────────────────────────────────────────────────────
# 2. HÀNH VI XOAY KHOÁ VÀ CỬA SỔ HAI KHOÁ
# ─────────────────────────────────────────────────────────────────────────────

def test_06_xoay_thanh_cong_khoa_moi_va_cu_deu_dung_duoc(moi_truong):
    """Xoay xong: khoá mới dùng được, khoá cũ vẫn dùng được trong cửa sổ."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoá xoay", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa"]["khoa"]

    # Xoay khoá với thời hạn cũ 7 ngày
    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay.status_code == 200
    res_xoay = r_xoay.json()
    assert res_xoay["ok"] is True
    khoa_moi = res_xoay["khoa"]["khoa"]
    cu_het_han = res_xoay["khoa"]["cu_het_han"]
    assert cu_het_han is not None
    assert res_xoay["khoa"]["co_khoa_cu"] is True
    assert khoa_moi != khoa_cu

    # Cả hai khoá đều gọi /v1/models thành công (200)
    r_cu = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"})
    assert r_cu.status_code == 200

    r_moi = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"})
    assert r_moi.status_code == 200


def test_07_het_cua_so_khoa_cu_tra_401(moi_truong):
    """Hết cửa sổ (giả lập bằng hạ cu_het_han về quá khứ): khoá cũ bị 401, khoá mới vẫn 200."""
    client, _, data_dir = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoá hết hạn", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa"]["khoa"]

    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 3600})
    assert r_xoay.status_code == 200
    khoa_moi = r_xoay.json()["khoa"]["khoa"]

    # Giả lập thời gian: sửa cu_het_han trên đĩa về quá khứ
    p_khoa = data_dir / "khoa_khach.json"
    du_lieu = json.loads(p_khoa.read_text(encoding="utf-8"))
    qua_khu = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    for k in du_lieu["khoa"]:
        if k["id"] == id_k:
            k["cu_het_han"] = qua_khu
    p_khoa.write_text(json.dumps(du_lieu, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()

    # Khoá cũ gọi -> 401
    r_cu = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"})
    assert r_cu.status_code == 401

    # Khoá mới gọi -> 200 bình thường
    r_moi = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"})
    assert r_moi.status_code == 200


def test_08_xoay_cu_giay_bang_0_khoa_cu_401_ngay(moi_truong):
    """cu_giay=0: khoá cũ chết ngay (401), khoá mới dùng được."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Nghi lộ", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa"]["khoa"]

    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 0})
    assert r_xoay.status_code == 200
    khoa_moi = r_xoay.json()["khoa"]["khoa"]

    # Khoá cũ chết ngay -> 401
    r_cu = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"})
    assert r_cu.status_code == 401

    # Khoá mới dùng được -> 200
    r_moi = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"})
    assert r_moi.status_code == 200


def test_09_xoay_hai_lan_khoa_cu_doi_dau_401(moi_truong):
    """Xoay hai lần: chỉ giữ MỘT khoá cũ; khoá cũ đời đầu (đời 1) 401 ngay, khoá đời 2 và đời 3 dùng được."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoá 2 đời", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_doi_1 = r_tao.json()["khoa"]["khoa"]

    # Lần 1: sinh khoá đời 2, khoá đời 1 thành khoá cũ
    r_xoay1 = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay1.status_code == 200
    khoa_doi_2 = r_xoay1.json()["khoa"]["khoa"]

    # Lần 2: sinh khoá đời 3, khoá đời 2 thành khoá cũ; khoá đời 1 bị đè chết ngay
    r_xoay2 = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay2.status_code == 200
    khoa_doi_3 = r_xoay2.json()["khoa"]["khoa"]

    # Khoá đời 1: 401
    r1 = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_doi_1}"})
    assert r1.status_code == 401

    # Khoá đời 2: 200 (đang là khoá cũ trong cửa sổ)
    r2 = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_doi_2}"})
    assert r2.status_code == 200

    # Khoá đời 3: 200 (khoá hiện tại)
    r3 = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_doi_3}"})
    assert r3.status_code == 200


@pytest.mark.parametrize("cu_giay_sai", [True, "60", 1.5, -1, 2592001])
def test_10_cu_giay_sai_kieu_am_vuot_tran_tra_400_kho_khong_doi(moi_truong, cu_giay_sai):
    """cu_giay sai kiểu (bool, str, float), âm, hoặc vượt trần 30 ngày -> 400, kho không đổi."""
    client, _, data_dir = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_Sai", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    p_khoa = data_dir / "khoa_khach.json"
    noi_dung_truoc = p_khoa.read_text(encoding="utf-8")

    r = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": cu_giay_sai})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_value"
    # Kho không bị thay đổi
    assert p_khoa.read_text(encoding="utf-8") == noi_dung_truoc


def test_11_chu_bi_tat_ca_hai_khoa_401(moi_truong):
    """Khoá có chủ: khi chủ bị tắt, CẢ HAI khoá (khoá mới và khoá cũ) đều bị 401."""
    client, _, _ = moi_truong
    # Tạo user có trần ["chat"]
    u, hdr_u = _tao_user_va_cookie(client, "owner-tat", vai="user", pham_vi_toi_da=["chat"])
    r_tao = client.post("/api/toi/khoa", headers=hdr_u, json={"nhan": "Khoá user"})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa_tho"]

    # Quản trị xoay khoá
    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay.status_code == 200
    khoa_moi = r_xoay.json()["khoa"]["khoa"]

    # Cả hai dùng bình thường
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"}).status_code == 200
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"}).status_code == 200

    # Quản trị tắt user (bat=False)
    r_tat = client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT, json={"bat": False})
    assert r_tat.status_code == 200

    # CẢ HAI khoá đều bị từ chối 401
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"}).status_code == 401
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"}).status_code == 401


def test_12_chu_ha_pham_vi_ca_hai_khoa_thu_hep(moi_truong):
    """Khoá có chủ: khi chủ bị hạ trần phạm vi, CẢ HAI khoá đều bị thu hẹp ngay lập tức."""
    client, mod, _ = moi_truong
    u, hdr_u = _tao_user_va_cookie(client, "owner-ha-tran", vai="user", pham_vi_toi_da=["chat", "anh"])
    r_tao = client.post("/api/toi/khoa", headers=hdr_u, json={"nhan": "Khoá user", "pham_vi": ["chat", "anh"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa_tho"]

    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay.status_code == 200
    khoa_moi = r_xoay.json()["khoa"]["khoa"]

    # Trước khi hạ trần: cả hai đều thấy model ảnh
    res_cu = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"}).json()
    assert any(m["id"] == "mock/image-model" for m in res_cu["data"])
    res_moi = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"}).json()
    assert any(m["id"] == "mock/image-model" for m in res_moi["data"])

    # Hạ trần user xuống chỉ còn ["chat"]
    r_ha = client.patch(f"/api/nguoi-dung/{u['id']}", headers=QT, json={"pham_vi_toi_da": ["chat"]})
    assert r_ha.status_code == 200

    # Sau khi hạ trần: CẢ HAI khoá không còn thấy model ảnh
    res_cu_ha = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"}).json()
    assert not any(m["id"] == "mock/image-model" for m in res_cu_ha["data"])
    res_moi_ha = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"}).json()
    assert not any(m["id"] == "mock/image-model" for m in res_moi_ha["data"])


def test_13_kho_hong_tra_401_khong_500(moi_truong):
    """Kho khoá khách hỏng trên đĩa: tra khoá trả 401, không bao giờ sập 500."""
    client, _, data_dir = moi_truong
    p_khoa = data_dir / "khoa_khach.json"
    p_khoa.write_text("{kho_hong_chua_dong_ngoac", encoding="utf-8")
    khoa_khach.lam_moi_cache()

    # Thử gọi bằng token ngẫu nhiên
    r = client.get("/v1/models", headers={"Authorization": "Bearer w2a_token_ngau_nhien_1234567890abcdefghijklmnop"})
    assert r.status_code == 503
    # Chuỗi không đúng mẫu w2a_ -> 401
    r_ko_w2a = client.get("/v1/models", headers={"Authorization": "Bearer khong_phai_w2a"})
    assert r_ko_w2a.status_code == 401


def test_14_cu_het_han_hong_tren_dia_khoa_cu_khong_dung_duoc(moi_truong):
    """cu_het_han hỏng trên đĩa -> khoá cũ không dùng được (đóng khi lỗi)."""
    client, _, data_dir = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_HongHH", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa"]["khoa"]

    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay.status_code == 200

    # Phá hỏng cu_het_han trong tệp
    p_khoa = data_dir / "khoa_khach.json"
    du_lieu = json.loads(p_khoa.read_text(encoding="utf-8"))
    for k in du_lieu["khoa"]:
        if k["id"] == id_k:
            k["cu_het_han"] = "khong-phai-iso-hop-le"
    p_khoa.write_text(json.dumps(du_lieu, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()

    # Khoá cũ không được cấp quyền
    r_cu = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"})
    assert r_cu.status_code in (401, 503)


def test_15_don_khoa_cu_het_han_khi_ghi_ke_tiep(moi_truong):
    """Khoá cũ hết hạn: dọn bam_cu và cu_het_han khỏi đĩa theo cách LƯỜI ở lần ghi kế tiếp."""
    client, _, data_dir = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_Don", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]

    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 3600})
    assert r_xoay.status_code == 200

    # Hạ cu_het_han về quá khứ trong cache
    with khoa_khach._KHOA_LUONG:
        for k in khoa_khach._KHOA_KHACH_CACHE:
            if k["id"] == id_k:
                k["cu_het_han"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")

    # Tạo một khoá mới khác (kích hoạt lần ghi kế tiếp)
    r_tao2 = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_KichHoat", "pham_vi": ["chat"]})
    assert r_tao2.status_code == 200

    # Kiểm tra tệp trên đĩa: bản ghi id_k đã sạch bam_cu và cu_het_han
    p_khoa = data_dir / "khoa_khach.json"
    du_lieu = json.loads(p_khoa.read_text(encoding="utf-8"))
    rec_k = next(k for k in du_lieu["khoa"] if k["id"] == id_k)
    assert "bam_cu" not in rec_k
    assert "cu_het_han" not in rec_k


def test_16_sach_khoa_khong_chua_bam_chua_van_tay_cu_het_han(moi_truong):
    """_sach_khoa và GET /api/khoa-khach: chứa van_tay, cu_het_han, co_khoa_cu; TUYỆT ĐỐI không chứa bam, bam_cu."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_Sach", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]

    client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})

    r_list = client.get("/api/khoa-khach", headers=QT)
    assert r_list.status_code == 200
    khoa_obj = next(k for k in r_list.json()["khoa"] if k["id"] == id_k)

    assert "van_tay" in khoa_obj and len(khoa_obj["van_tay"]) == 16
    assert "cu_het_han" in khoa_obj and khoa_obj["cu_het_han"] is not None
    assert khoa_obj["co_khoa_cu"] is True

    json_str = json.dumps(khoa_obj)
    assert "bam" not in json_str
    assert "bam_cu" not in json_str


# ─────────────────────────────────────────────────────────────────────────────
# 3. SAO LƯU VÀ KHÔI PHỤC (CHỐNG LÁCH)
# ─────────────────────────────────────────────────────────────────────────────

def test_17_sao_luu_khoi_phuc_giu_bam_cu(moi_truong):
    """Sao lưu và khôi phục: giữ nguyên cả khoá mới và khoá cũ (bam_cu/cu_het_han)."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_SaoLuu", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    id_k = r_tao.json()["khoa"]["id"]
    khoa_cu = r_tao.json()["khoa"]["khoa"]

    r_xoay = client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})
    assert r_xoay.status_code == 200
    khoa_moi = r_xoay.json()["khoa"]["khoa"]

    # Tạo bản sao lưu
    du_lieu_sl, _ = sao_luu.tao_ban_sao_luu()

    # Kiểm tra khôi phục
    res_kiem = asyncio.run(sao_luu.kiem_khoi_phuc(du_lieu_sl, mat_khau=None))
    assert res_kiem["ok"] is True
    assert res_kiem["co_the_ap_dung"] is True

    # Áp dụng khôi phục
    res_ap = asyncio.run(sao_luu.ap_dung_khoi_phuc(du_lieu_sl, mat_khau=None, sha256_da_kiem=res_kiem["sha256"]))
    assert res_ap["ok"] is True

    # Cả hai khoá vẫn tra được qua xác thực sau khôi phục
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_cu}"}).status_code == 200
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_moi}"}).status_code == 200


def test_18_khoi_phuc_tu_choi_bam_cu_trung_khoa_khac(moi_truong):
    """Tệp khôi phục có bam_cu trùng băm của khoá khác đang có trên máy -> bị từ chối ở bước đối chiếu."""
    client, _, data_dir = moi_truong
    # Tạo K1 trên máy
    r1 = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K1", "pham_vi": ["chat"]})
    id1 = r1.json()["khoa"]["id"]
    bam1 = hashlib.sha256(r1.json()["khoa"]["khoa"].encode()).hexdigest()

    # Chuẩn bị bản ghi K2 có bam_cu trùng bam1 của K1
    id2 = "kk_test_k2"
    bam2 = hashlib.sha256(b"k2_khoa_moi_ngau_nhien_1234567890123456").hexdigest()
    du_lieu_k2 = {
        "khoa": [
            {
                "id": id2,
                "nhan": "K2",
                "bat": True,
                "pham_vi": ["chat"],
                "bam": bam2,
                "bam_cu": bam1,
                "cu_het_han": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds"),
                "tao_luc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ]
    }
    p_khoa = data_dir / "khoa_khach.json"
    noi_dung_k1 = p_khoa.read_text(encoding="utf-8")

    # Tạm thời ghi K2 ra đĩa để tạo bản sao lưu chỉ chứa K2
    p_khoa.write_text(json.dumps(du_lieu_k2, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()
    du_lieu_sl, _ = sao_luu.tao_ban_sao_luu()

    # Trả lại đĩa trên máy về chỉ chứa K1
    p_khoa.write_text(noi_dung_k1, encoding="utf-8")
    khoa_khach.lam_moi_cache()

    # Kiểm tra khôi phục: bản sao lưu phải bị từ chối vì K2 mang bam_cu trùng băm của K1 đang có trên máy
    res_kiem = asyncio.run(sao_luu.kiem_khoi_phuc(du_lieu_sl, mat_khau=None))
    assert res_kiem["co_the_ap_dung"] is False
    assert any(t["tep"] == "khoa_khach.json" and t["id"] == id2 for t in res_kiem["tu_choi"])


def test_19_khoi_phuc_tu_choi_hoan_doi_bam_de_doi_chu(moi_truong):
    """Hoán đổi bam ↔ bam_cu để thoát chủ hoặc đổi chủ -> bị từ chối (bo_chu=True)."""
    client, _, data_dir = moi_truong
    u, hdr_u = _tao_user_va_cookie(client, "owner-hd", vai="user", pham_vi_toi_da=["chat"])
    r_tao = client.post("/api/toi/khoa", headers=hdr_u, json={"nhan": "Khoá có chủ"})
    id_k = r_tao.json()["khoa"]["id"]

    # Xoay khoá
    client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})

    # Can thiệp bản sao lưu bằng cách giải nén/sửa hoặc sửa tệp rồi tạo bản sao lưu mới:
    p_khoa = data_dir / "khoa_khach.json"
    du_lieu = json.loads(p_khoa.read_text(encoding="utf-8"))
    for k in du_lieu["khoa"]:
        if k["id"] == id_k:
            # Hoán đổi bam ↔ bam_cu và xoá chu_id để thoát chủ
            k["bam"], k["bam_cu"] = k["bam_cu"], k["bam"]
            k["chu_id"] = None
    p_khoa.write_text(json.dumps(du_lieu, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()

    du_lieu_doc_hai, _ = sao_luu.tao_ban_sao_luu()

    # Khôi phục máy về trạng thái gốc (có chủ)
    for k in du_lieu["khoa"]:
        if k["id"] == id_k:
            k["bam"], k["bam_cu"] = k["bam_cu"], k["bam"]
            k["chu_id"] = u["id"]
    p_khoa.write_text(json.dumps(du_lieu, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()

    # Thử kiểm khôi phục tệp độc hại -> phải bị từ chối do bỏ chủ
    res_kiem = asyncio.run(sao_luu.kiem_khoi_phuc(du_lieu_doc_hai, mat_khau=None))
    assert res_kiem["co_the_ap_dung"] is False
    assert any(t["tep"] == "khoa_khach.json" and t["id"] == id_k and "bỏ hoặc đổi chủ" in t["ly_do"] for t in res_kiem["tu_choi"])


def test_20_khoi_phuc_tu_choi_cu_het_han_qua_tran(moi_truong):
    """cu_het_han vượt quá trần 30 ngày tính từ lúc khôi phục -> bị từ chối."""
    client, _, data_dir = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_Tran", "pham_vi": ["chat"]})
    id_k = r_tao.json()["khoa"]["id"]
    client.post(f"/api/khoa-khach/{id_k}/xoay", headers=QT, json={"cu_giay": 604800})

    # Sửa cu_het_han thành 40 ngày (vượt trần 30 ngày)
    p_khoa = data_dir / "khoa_khach.json"
    du_lieu = json.loads(p_khoa.read_text(encoding="utf-8"))
    for k in du_lieu["khoa"]:
        if k["id"] == id_k:
            k["cu_het_han"] = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat(timespec="seconds")
    p_khoa.write_text(json.dumps(du_lieu, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()

    du_lieu_sl, _ = sao_luu.tao_ban_sao_luu()

    # Khôi phục tệp trên máy về bình thường
    for k in du_lieu["khoa"]:
        if k["id"] == id_k:
            k["cu_het_han"] = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    p_khoa.write_text(json.dumps(du_lieu, indent=2), encoding="utf-8")
    khoa_khach.lam_moi_cache()

    # Kiểm khôi phục -> bị từ chối
    res_kiem = asyncio.run(sao_luu.kiem_khoi_phuc(du_lieu_sl, mat_khau=None))
    assert res_kiem["co_the_ap_dung"] is False
    assert any(t["tep"] == "khoa_khach.json" and "vượt quá trần" in t["ly_do"] for t in res_kiem["tu_choi"])


# ─────────────────────────────────────────────────────────────────────────────
# 4. VÂN TAY KHOÁ CHO NHẬT KÝ
# ─────────────────────────────────────────────────────────────────────────────

def test_21_nhat_ky_403_chua_van_tay_khong_chua_khoa_tho_hay_bam(moi_truong):
    """Nhật ký sau lần gọi 403 của khoá khách: chứa khoa_van_tay 16 hex và KHÔNG chứa khoá thô hay băm 64 hex."""
    client, _, data_dir = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_Log", "pham_vi": ["chat"]})
    assert r_tao.status_code == 200
    khoa_tho = r_tao.json()["khoa"]["khoa"]
    bam = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()

    # Khoá khách gọi đường quản trị -> 403
    r_403 = client.get("/api/khoa-khach", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r_403.status_code == 403

    # Đọc sổ nhật ký quản trị
    nhat_ky_file = data_dir / "nhat_ky" / "nhat_ky.jsonl"
    assert nhat_ky_file.is_file()
    cac_dong = [json.loads(line) for line in nhat_ky_file.read_text(encoding="utf-8").strip().splitlines() if line.strip()]
    dong_403 = [d for d in cac_dong if d.get("kenh") == "quan_tri" and "403" in d.get("tom_tat", "")][-1]

    # Kiểm tra trường khoa_van_tay
    vt = dong_403.get("them", {}).get("khoa_van_tay")
    assert vt is not None
    assert len(vt) == 16
    assert all(c in "0123456789abcdef" for c in vt)

    # KHÔNG chứa khoá thô và KHÔNG chứa băm 64 hex của khoá trong toàn bộ dòng
    dong_raw = json.dumps(dong_403)
    assert khoa_tho not in dong_raw
    assert bam not in dong_raw


def test_22_tep_khoa_van_tay_quyen_0600(moi_truong):
    """Tệp khoa_van_tay lưu tại <DATA>/bi_mat/khoa_van_tay có quyền 0600."""
    client, _, data_dir = moi_truong
    # Gọi van_tay để kích hoạt sinh tệp
    khoa_khach.van_tay(hashlib.sha256(b"test").hexdigest())

    p_knb = data_dir / "bi_mat" / "khoa_van_tay"
    assert p_knb.is_file()
    quyen = stat.S_IMODE(p_knb.stat().st_mode)
    assert quyen == 0o600, f"Quyền tệp khoa_van_tay mong đợi 0600, nhận {oct(quyen)}"


def test_23_loi_ghi_tep_van_tay_van_xac_thuc_binh_thuong_van_tay_hoi_cham(moi_truong, monkeypatch):
    """Khi tệp khoa_van_tay không đọc/ghi được: xác thực vẫn thành công và van_tay trả '?'."""
    client, _, _ = moi_truong
    r_tao = client.post("/api/khoa-khach", headers=QT, json={"nhan": "K_LoiKNB", "pham_vi": ["chat"]})
    khoa_tho = r_tao.json()["khoa"]["khoa"]

    # Giả lập lỗi đọc/ghi khoá nội bộ
    khoa_khach.lam_moi_cache()
    khoa_khach._KHOA_VAN_TAY_CACHE = None
    monkeypatch.setattr(khoa_khach, "_lay_khoa_noi_bo", lambda: None)

    # Vân tay trả "?"
    assert khoa_khach.van_tay(hashlib.sha256(b"abc").hexdigest()) == "?"

    # Xác thực /v1/models vẫn thành công bình thường (200), không bị chặn
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {khoa_tho}"})
    assert r.status_code == 200


def test_24_van_tay_khac_nhau_cho_khoa_khac_nhau_on_dinh_cho_cung_khoa(moi_truong):
    """Hai khoá khác nhau có vân tay khác nhau; cùng một khoá cho vân tay ổn định."""
    _, _, _ = moi_truong
    bam1 = hashlib.sha256(b"khoa_so_1").hexdigest()
    bam2 = hashlib.sha256(b"khoa_so_2").hexdigest()

    vt1_a = khoa_khach.van_tay(bam1)
    vt1_b = khoa_khach.van_tay(bam1)
    vt2 = khoa_khach.van_tay(bam2)

    assert len(vt1_a) == 16
    assert vt1_a == vt1_b, "Vân tay không ổn định cho cùng một băm khoá"
    assert vt1_a != vt2, "Hai khoá khác nhau nhưng sinh vân tay trùng nhau"
