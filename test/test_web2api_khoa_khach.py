"""Kiểm thử tính năng Khoá khách có phạm vi và Sổ thao tác quản trị (Việc E).

Bao gồm:
- Kịch bản F.4: tạo khoá khách, kiểm tra phạm vi, thu hẹp phạm vi, lọc models, tắt/thu hồi.
- Khách gọi đường quản trị /api/* và trang quản trị → 403 (không lộ dữ liệu).
- Phạm vi chat gọi ảnh → 403 insufficient_scope.
- Khoá khách chỉ hiện MỘT LẦN duy nhất khi tạo; không xuất hiện ở bất kỳ phản hồi, nhật ký hay tệp đĩa nào ngoài bản băm.
- Thân gửi sang module không chứa danh tính khách (request.state do lõi giữ).
- Trần đồng thời WEB2API_KHACH_DONG_THOI → 429 + Retry-After (không làm hỏng trạng thái module).
- Đo µs đường nóng: tìm khoá và đếm lượt trong RAM cực nhanh, không ghi đĩa mỗi lượt.
- Sổ thao tác quản trị: kênh nhật ký quan_tri ghi nhận tạo/sửa/thu hồi và từ chối 401/403.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nhat_ky, xac_thuc
from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


class ModuleMockF4:
    """Module giả lập phục vụ kịch bản kiểm thử F.4."""

    def __init__(self, tien_to: str = "mock"):
        self.tien_to = tien_to
        self.than_chat_nhan_duoc: list[dict] = []
        self.than_anh_nhan_duoc: list[dict] = []
        self.khoa_dang_chay = True

    async def chat(self, body: dict, stream: bool = False):
        self.than_chat_nhan_duoc.append(dict(body))
        tra_ve = {
            "id": "chatcmpl-mock123",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", f"{self.tien_to}/chat-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Xin chào từ mock module"},
                    "finish_reason": "stop",
                }
            ],
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")

    async def anh(self, body: dict):
        self.than_anh_nhan_duoc.append(dict(body))
        tra_ve = {
            "created": int(time.time()),
            "data": [{"url": "http://example.com/mock-image.png"}],
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")

    async def models(self) -> list[dict]:
        return [
            {
                "id": f"{self.tien_to}/chat-model",
                "object": "model",
                "created": 1234567890,
                "owned_by": "mock",
                "kha_nang": ["chat"],
            },
            {
                "id": f"{self.tien_to}/image-model",
                "object": "model",
                "created": 1234567890,
                "owned_by": "mock",
                "kha_nang": ["anh"],
            },
        ]

    async def suc_khoe(self) -> dict:
        return {"ok": True, "khoa": [{"khoa": "…mock", "ok": True, "do_tre_ms": 10}]}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Đăng ký mock module vào app.state.so
    mod = ModuleMockF4("mock")
    so = SoModule()
    so.tu_xa["mock"] = {"tien_to": "mock", "url": "http://mock"}
    so.dang_chay["mock"] = mod

    with TestClient(app) as client:
        client.app.state.so = so
        yield client, mod, data_dir


def test_kich_ban_f4_khoa_khach_toan_dien(moi_truong):
    """Kịch bản F.4: Vòng đời khoá khách, phạm vi chat/ảnh, lọc models, tắt và thu hồi."""
    client, mod, data_dir = moi_truong

    # 1. Quản trị tạo khoá khách chỉ có phạm vi "chat"
    r = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Ứng dụng Chat phòng khách", "pham_vi": ["chat"]},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True
    k_info = res["khoa"]
    id_khoa = k_info["id"]
    khoa_tho = k_info["khoa"]
    assert khoa_tho.startswith("w2a_")
    assert k_info["nhan"] == "Ứng dụng Chat phòng khách"
    assert k_info["pham_vi"] == ["chat"]
    assert k_info["bat"] is True
    assert k_info["so_luot"] == 0

    hd_khach = {"Authorization": f"Bearer {khoa_tho}"}

    # 2. GET /v1/models: Tự động lọc, chỉ thấy model có khả năng chat
    r_models = client.get("/v1/models", headers=hd_khach)
    assert r_models.status_code == 200
    ds_models = r_models.json()["data"]
    ids = [m["id"] for m in ds_models]
    assert "mock/chat-model" in ids
    assert "mock/image-model" not in ids  # Đã bị lọc bỏ vì không có quyền anh

    # 3. Khách gọi /v1/chat/completions: Thành công
    r_chat = client.post(
        "/v1/chat/completions",
        headers=hd_khach,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_chat.status_code == 200
    assert "Xin chào từ mock module" in r_chat.json()["choices"][0]["message"]["content"]

    # Kiểm tra thân tới module KHÔNG chứa danh tính khách
    assert len(mod.than_chat_nhan_duoc) == 1
    than_mod = mod.than_chat_nhan_duoc[0]
    assert "nguoi_goi" not in than_mod
    assert "id_khach" not in than_mod
    assert "danh_tinh" not in than_mod
    assert "khach" not in than_mod

    # 4. Khách gọi /v1/images/generations: Bị 403 insufficient_scope (vì phạm vi chỉ có chat)
    r_anh = client.post(
        "/v1/images/generations",
        headers=hd_khach,
        json={"model": "mock/image-model", "prompt": "Một chú mèo con"},
    )
    assert r_anh.status_code == 403
    loi = r_anh.json()["error"]
    assert loi["type"] == "insufficient_scope"
    assert loi["code"] == "insufficient_scope"
    assert "chat" in k_info["pham_vi"] and "anh" not in k_info["pham_vi"]

    # 5. Khách tự thu hẹp phạm vi qua header X-Web2api-Scope
    # Khoá có ["chat"], thu hẹp xuống ["chat"] → OK
    r_chat_hep = client.post(
        "/v1/chat/completions",
        headers={**hd_khach, "X-Web2api-Scope": "chat"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo 2"}]},
    )
    assert r_chat_hep.status_code == 200

    # Khách cố tình mở rộng phạm vi lên "anh" → 403 insufficient_scope
    r_rong = client.post(
        "/v1/chat/completions",
        headers={**hd_khach, "X-Web2api-Scope": "chat,anh"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Hack"}]},
    )
    assert r_rong.status_code == 403
    assert r_rong.json()["error"]["code"] == "insufficient_scope"

    # 6. Tắt khoá (bat: False) → gọi /v1/* lập tức bị 401
    r_tat = client.patch(f"/api/khoa-khach/{id_khoa}", headers=QT, json={"bat": False})
    assert r_tat.status_code == 200
    assert r_tat.json()["khoa"]["bat"] is False

    r_chat_tat = client.post(
        "/v1/chat/completions",
        headers=hd_khach,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_chat_tat.status_code == 401
    assert r_chat_tat.json()["error"]["code"] == "invalid_api_key"

    # Bật lại khoá → gọi lại thành công
    client.patch(f"/api/khoa-khach/{id_khoa}", headers=QT, json={"bat": True})
    r_chat_bat = client.post(
        "/v1/chat/completions",
        headers=hd_khach,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_chat_bat.status_code == 200

    # 7. Thu hồi khoá (DELETE) → lập tức bị 401
    r_xoa = client.delete(f"/api/khoa-khach/{id_khoa}", headers=QT)
    assert r_xoa.status_code == 200
    assert r_xoa.json()["da_xoa"] is True

    r_chat_xoa = client.post(
        "/v1/chat/completions",
        headers=hd_khach,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_chat_xoa.status_code == 401
    assert r_chat_xoa.json()["error"]["code"] == "invalid_api_key"


def test_khach_goi_api_quan_tri_nhan_403(moi_truong):
    """Khách gọi bất kỳ đường quản trị /api/* hoặc trang quản trị đều nhận mã 403."""
    client, _, _ = moi_truong

    # Tạo một khoá khách
    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khách thử quyền", "pham_vi": ["*"]})
    khoa_tho = r.json()["khoa"]["khoa"]
    hd_khach = {"Authorization": f"Bearer {khoa_tho}"}

    cac_duong_quan_tri = [
        ("GET", "/api/modules"),
        ("GET", "/api/modules/catalog"),
        ("GET", "/api/tinh-nang"),
        ("GET", "/api/he-thong"),
        ("GET", "/api/khoa-khach"),
        ("POST", "/api/khoa-khach"),
        ("PATCH", "/api/khoa-khach/kk_test"),
        ("DELETE", "/api/khoa-khach/kk_test"),
        ("GET", "/api/nhat-ky"),
        ("GET", "/api/nhat-ky/kenh"),
        ("DELETE", "/api/nhat-ky"),
        ("GET", "/api/nguon-git"),
        ("PUT", "/api/nguon-git"),
        ("GET", "/api/dia-chi"),
        ("PUT", "/api/dia-chi"),
        ("GET", "/api/ket-noi"),
        ("GET", "/He-thong.dc.html"),
        ("GET", "/He-thong-AI.dc.html"),
        ("GET", "/He-thong-Bao-mat.dc.html"),
    ]

    for phuong_thuc, duong in cac_duong_quan_tri:
        res = client.request(phuong_thuc, duong, headers=hd_khach, json={})
        assert res.status_code == 403, f"{phuong_thuc} {duong} không trả 403, nhận: {res.status_code}"
        loi = res.json()["error"]
        assert loi["type"] == "permission_error"
        assert loi["code"] == "forbidden"


def test_khoa_chi_hien_mot_lan_khong_lot_ra_ngoai(moi_truong):
    """Khoá chỉ hiện MỘT LẦN khi tạo; không xuất hiện ở tệp đĩa, phản hồi hay nhật ký."""
    client, _, data_dir = moi_truong

    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khoá bảo mật", "pham_vi": ["chat"]})
    assert r.status_code == 200
    khoa_tho = r.json()["khoa"]["khoa"]
    id_khoa = r.json()["khoa"]["id"]
    assert khoa_tho.startswith("w2a_")

    # 1. GET /api/khoa-khach không chứa khoá thô và không chứa bản băm
    r_get = client.get("/api/khoa-khach", headers=QT)
    than_get = r_get.text
    assert khoa_tho not in than_get
    ds = r_get.json()["khoa"]
    muc = next(k for k in ds if k["id"] == id_khoa)
    assert "khoa" not in muc
    assert "bam" not in muc

    # 2. PATCH /api/khoa-khach không chứa khoá thô
    r_patch = client.patch(f"/api/khoa-khach/{id_khoa}", headers=QT, json={"nhan": "Đổi tên"})
    assert khoa_tho not in r_patch.text
    assert "bam" not in r_patch.json()["khoa"]

    # 3. Tệp trên đĩa <DATA>/khoa_khach.json không chứa khoá thô
    p_tep = data_dir / "khoa_khach.json"
    assert p_tep.exists()
    noi_dung_tep = p_tep.read_text(encoding="utf-8")
    assert khoa_tho not in noi_dung_tep
    # Tệp có quyền 0600
    assert stat.S_IMODE(p_tep.stat().st_mode) == 0o600

    # 4. Gọi /v1/chat/completions và kiểm tra nhật ký
    client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {khoa_tho}"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    r_nk = client.get("/api/nhat-ky?gioi_han=50", headers=QT)
    than_nk = r_nk.text
    assert khoa_tho not in than_nk

    # 5. Nhật ký quan_tri ghi nhận hành động tạo/sửa nhưng không có khoá
    r_nk_qt = client.get("/api/nhat-ky?kenh=quan_tri", headers=QT)
    assert khoa_tho not in r_nk_qt.text
    ban_ghi_qt = r_nk_qt.json()
    assert isinstance(ban_ghi_qt, list)
    assert any("Tạo khoá khách" in b.get("tom_tat", "") for b in ban_ghi_qt)
    assert any("Cập nhật khoá khách" in b.get("tom_tat", "") for b in ban_ghi_qt)


def test_tran_dong_thoi_khach(moi_truong, monkeypatch):
    """Trần đồng thời WEB2API_KHACH_DONG_THOI → 429 rate_limit_exceeded kèm Retry-After: 1."""
    client, _, _ = moi_truong
    monkeypatch.setenv("WEB2API_KHACH_DONG_THOI", "1")

    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khách đồng thời", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]
    hd = {"Authorization": f"Bearer {k_tho}"}

    # Chiếm vị trí đồng thời thứ 1
    khoa_khach.vao_dong_thoi(id_k)
    try:
        # Lượt thứ 2 gửi đến sẽ vượt trần (tối đa 1) → 429
        r2 = client.post(
            "/v1/chat/completions",
            headers=hd,
            json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Test 429"}]},
        )
        assert r2.status_code == 429
        assert r2.headers.get("retry-after") == "1"
        loi = r2.json()["error"]
        assert loi["type"] == "rate_limit_error"
        assert loi["code"] == "rate_limit_exceeded"
    finally:
        # Giải phóng vị trí đồng thời
        khoa_khach.ra_dong_thoi(id_k)

    # Sau khi giải phóng, yêu cầu tiếp theo thành công bình thường
    r3 = client.post(
        "/v1/chat/completions",
        headers=hd,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Hết nghẽn"}]},
    )
    assert r3.status_code == 200


def test_do_micro_giay_duong_nong(moi_truong):
    """Đo µs đường nóng: tìm khoá và đếm lượt trong RAM cực nhanh (<100 µs), không ghi đĩa mỗi lượt."""
    client, _, data_dir = moi_truong

    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Đo hiệu năng", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]

    p_tep = data_dir / "khoa_khach.json"
    st_truoc = p_tep.stat()

    SO_LUOT = 3000
    bat_dau = time.perf_counter()
    for _ in range(SO_LUOT):
        k = khoa_khach.tim_theo_khoa_tho(k_tho)
        assert k is not None
        khoa_khach.ghi_nhan_su_dung(id_k)
    tong_giay = time.perf_counter() - bat_dau

    us_moi_luot = (tong_giay / SO_LUOT) * 1_000_000
    # Đường nóng trong RAM phải dưới 100 µs mỗi lượt (thường < 20 µs)
    assert us_moi_luot < 100, f"Đường nóng quá chậm: {us_moi_luot:.2f} µs"

    # Tệp trên đĩa KHÔNG bị ghi trong suốt SO_LUOT cuộc gọi đường nóng
    st_sau = p_tep.stat()
    assert st_sau.st_mtime_ns == st_truoc.st_mtime_ns
    assert st_sau.st_ino == st_truoc.st_ino

    # Khi gọi dong_bo_dia(), dữ liệu mới được ghi ra đĩa
    khoa_khach.dong_bo_dia()
    st_dong_bo = p_tep.stat()
    assert st_dong_bo.st_mtime_ns != st_truoc.st_mtime_ns

    # Đọc lại tệp trên đĩa: số lượt đã được cập nhật đủ SO_LUOT
    du_lieu = kho.doc_json(p_tep)
    muc = next(k for k in du_lieu["khoa"] if k["id"] == id_k)
    assert muc["so_luot"] == SO_LUOT
    assert muc["dung_cuoi"] is not None


def test_so_thao_tac_quan_tri_va_danh_tinh_cuoc_goi(moi_truong):
    """Sổ thao tác: middleware cuoc_goi ghi danh tính (quan_tri, khach:<id>, chua_xac_thuc)."""
    client, _, _ = moi_truong

    # 1. Yêu cầu của quản trị
    client.get("/v1/models", headers=QT)

    # 2. Yêu cầu của khách
    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khách sổ thao tác", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]
    client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {k_tho}"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Hi"}]},
    )

    # 3. Yêu cầu chưa xác thực (sai khoá)
    client.get("/v1/models", headers={"Authorization": "Bearer sai-khoa-123"})

    # Kiểm tra nhật ký cuoc_goi
    r_nk = client.get("/api/nhat-ky?kenh=cuoc_goi&gioi_han=10", headers=QT)
    assert r_nk.status_code == 200
    ds = r_nk.json()
    assert isinstance(ds, list)
    danh_tinh_ds = [b.get("them", {}).get("nguoi_goi") for b in ds]
    assert "quan_tri" in danh_tinh_ds
    assert f"khach:{id_k}" in danh_tinh_ds
    assert "chua_xac_thuc" in danh_tinh_ds
