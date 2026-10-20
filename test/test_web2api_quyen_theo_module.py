"""Kiểm thử tính năng QUYỀN THEO MODULE cho khoá khách và trần người dùng.

Bao gồm:
1. Cú pháp phạm vi mở rộng: «<kha_nang>@<module>» (hợp lệ/không hợp lệ).
2. Kiểm tra module tồn tại khi tạo/sửa khoá khách và trần người dùng (module lạ -> 400).
3. Leo thang quyền giữa các module: khách có chat@mod-a không gọi được mod-b (403), không thấy model mod-b.
4. Module bị gỡ sau khi tạo khoá -> đóng quyền trên module đó (403).
5. Khoá «*» KHÔNG còn bao gồm nhóm nguy hiểm (thiet_bi:ghi, trinh_duyet, gui_ra/gui_tin...) -> 403.
6. Cấp đích danh khả năng nguy hiểm (thiet_bi:ghi hoặc thiet_bi:ghi@mod) -> thành công.
7. Trần quyền user (pham_vi_toi_da) áp dụng cho cú pháp mới, user không tự cấp quyền vượt trần.
8. Chống IDOR và khoá khách không thể tự tạo khoá (401).
9. Test nguyên tắc duyệt mọi route V1_AUTH: khẳng định có nhận (khả năng, module) khi gọi.
10. Kiểm tra đột biến (mutations test): bỏ kiểm module hoặc bỏ loại nguy hiểm khỏi «*» -> ĐỎ.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nguoi_dung, phien_web, xac_thuc
from app.kham_pha import KHA_NANG_NGUY_HIEM
from app.main import app
from app.so_module import SoModule
from app.xac_thuc import can_quan_tri, khop_pham_vi, kiem_tra_pham_vi, la_kha_nang_nguy_hiem, xac_thuc_v1

# Chuỗi khoá giả nằm trong allowlist của .gitleaks.toml
KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


class ModuleMockDonGian:
    """Mock module phục vụ kiểm thử phân quyền theo module."""

    def __init__(self, mod_id: str):
        self.mod_id = mod_id
        self.tien_to = mod_id.replace("-", "_")
        self.khoa_dang_chay = True

    async def chat(self, body: dict, stream: bool = False):
        tra_ve = {
            "id": f"chatcmpl-{self.mod_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", f"{self.tien_to}/chat-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"Xin chào từ {self.mod_id}"},
                    "finish_reason": "stop",
                }
            ],
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")

    async def models(self) -> list[dict]:
        return [
            {
                "id": f"{self.tien_to}/chat-model",
                "object": "model",
                "created": 1234567890,
                "owned_by": self.mod_id,
                "kha_nang": ["chat"],
            }
        ]

    async def suc_khoe(self) -> dict:
        return {"ok": True}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")

    # Tạo sổ module với 2 module mẫu
    so = SoModule()
    mod_a = ModuleMockDonGian("mod-a")
    mod_b = ModuleMockDonGian("mod-b")

    km_a = MagicMock()
    km_a.tien_to = "mod_a"
    km_a.chuan_loi = {}
    km_a.manifest = {
        "id": "mod-a",
        "ten": "Module A",
        "cung_cap": ["chat"],
        "nguy_hiem": ["chay_ma"],
    }
    km_b = MagicMock()
    km_b.tien_to = "mod_b"
    km_b.chuan_loi = {}
    km_b.manifest = {
        "id": "mod-b",
        "ten": "Module B",
        "cung_cap": ["chat"],
        "nguy_hiem": [],
    }

    so.catalog["mod-a"] = km_a
    so.catalog["mod-b"] = km_b
    so.dang_chay["mod-a"] = mod_a
    so.dang_chay["mod-b"] = mod_b

    # Reset cache khoá khách và người dùng
    khoa_khach._KHOA_KHACH_CACHE.clear()
    khoa_khach._KHOA_KHACH_CACHE_MTIME = None

    with TestClient(app) as client:
        client.app.state.so = so
        yield client, so


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 1: CÚ PHÁP VÀ KIỂM TRA MODULE TỒN TẠI KHI TẠO/SỬA KHOÁ, TRẦN USER
# ─────────────────────────────────────────────────────────────────────────────

def test_01_cu_phap_la_ma_pham_vi():
    """Kiểm tra đơn vị cú pháp mã phạm vi mở rộng «<kha_nang>@<module>»."""
    # Hợp lệ
    assert khoa_khach.la_ma_pham_vi("*") is True
    assert khoa_khach.la_ma_pham_vi("chat") is True
    assert khoa_khach.la_ma_pham_vi("thiet_bi:doc") is True
    assert khoa_khach.la_ma_pham_vi("thiet_bi:ghi") is True
    assert khoa_khach.la_ma_pham_vi("chat@mod-a") is True
    assert khoa_khach.la_ma_pham_vi("*@mod-a") is True
    assert khoa_khach.la_ma_pham_vi("thiet_bi:ghi@mod-a") is True
    assert khoa_khach.la_ma_pham_vi("gui_tin@mod-a") is True

    # Không hợp lệ
    assert khoa_khach.la_ma_pham_vi("") is False
    assert khoa_khach.la_ma_pham_vi(None) is False
    assert khoa_khach.la_ma_pham_vi(123) is False
    assert khoa_khach.la_ma_pham_vi("chat@") is False
    assert khoa_khach.la_ma_pham_vi("@mod-a") is False
    assert khoa_khach.la_ma_pham_vi("chat@mod@mod") is False
    assert khoa_khach.la_ma_pham_vi("chat:") is False
    assert khoa_khach.la_ma_pham_vi(":chat") is False
    assert khoa_khach.la_ma_pham_vi("chat::doc") is False
    assert khoa_khach.la_ma_pham_vi("chat@_mod") is False  # ID_HOP_LE bắt đầu [a-z0-9]
    assert khoa_khach.la_ma_pham_vi("chat@catalog") is False  # ID_DANH_RIENG
    assert khoa_khach.la_ma_pham_vi("chat@tu-xa") is False  # ID_DANH_RIENG


def test_02_chuan_hoa_pham_vi_kiem_tra_module(moi_truong):
    """Kiểm tra chuan_hoa_pham_vi ném 400 module_not_found khi module không tồn tại."""
    client, so = moi_truong

    # Module lạ -> 400
    with pytest.raises(khoa_khach.LoiOpenAI) as exc:
        khoa_khach.chuan_hoa_pham_vi(["chat@module-la-999"], cho_phep_rong=False, kiem_tra_module=True, so=so)
    assert exc.value.ma == 400
    assert exc.value.code == "module_not_found"

    # Module hợp lệ đã đăng ký trong catalog -> hợp lệ
    pv = khoa_khach.chuan_hoa_pham_vi(["chat@mod-a"], cho_phep_rong=False, kiem_tra_module=True, so=so)
    assert pv == ["chat@mod-a"]


def test_03_api_tao_va_sua_khoa_khach_module_khong_ton_tai_tra_400(moi_truong):
    """API tạo/sửa khoá khách: gửi module không tồn tại -> 400 module_not_found; cú pháp lạ -> 400 invalid_value."""
    client, so = moi_truong

    # Cú pháp lạ -> 400 invalid_value
    res_cu_phap = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Test cú pháp", "pham_vi": ["chat@"]},
    )
    assert res_cu_phap.status_code == 400
    assert res_cu_phap.json()["error"]["code"] == "invalid_value"

    # Module lạ -> 400 module_not_found
    res_la = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Test module lạ", "pham_vi": ["chat@module-khong-ton-tai"]},
    )
    assert res_la.status_code == 400
    assert res_la.json()["error"]["code"] == "module_not_found"

    # Module hợp lệ -> 200 thành công
    res_tao = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Test hợp lệ", "pham_vi": ["chat@mod-a"]},
    )
    assert res_tao.status_code == 200
    kid = res_tao.json()["khoa"]["id"]

    # Sửa với module lạ -> 400 module_not_found
    res_sua_la = client.patch(
        f"/api/khoa-khach/{kid}",
        headers=QT,
        json={"pham_vi": ["chat@module-khong-ton-tai"]},
    )
    assert res_sua_la.status_code == 400
    assert res_sua_la.json()["error"]["code"] == "module_not_found"


def test_04_api_tao_va_sua_user_tran_module_khong_ton_tai_tra_400(moi_truong):
    """API tạo/sửa user: trần quyền (pham_vi_toi_da) chứa module lạ -> 400 module_not_found."""
    client, so = moi_truong

    # Tạo user với module lạ -> 400
    res_user_la = client.post(
        "/api/nguoi-dung",
        headers=QT,
        json={
            "ten_dang_nhap": "user_test_la",
            "mat_khau": "MatKhau123!@#",
            "vai": "user",
            "pham_vi_toi_da": ["chat@module-khong-co"],
        },
    )
    assert res_user_la.status_code == 400
    assert res_user_la.json()["error"]["code"] == "module_not_found"

    # Tạo user hợp lệ với mod-a
    res_user_ok = client.post(
        "/api/nguoi-dung",
        headers=QT,
        json={
            "ten_dang_nhap": "user_test_ok",
            "mat_khau": "MatKhau123!@#",
            "vai": "user",
            "pham_vi_toi_da": ["chat@mod-a"],
        },
    )
    assert res_user_ok.status_code == 200
    uid = res_user_ok.json()["nguoi_dung"]["id"]

    # Sửa user với module lạ -> 400
    res_sua_la = client.patch(
        f"/api/nguoi-dung/{uid}",
        headers=QT,
        json={"pham_vi_toi_da": ["chat@module-khong-co"]},
    )
    assert res_sua_la.status_code == 400
    assert res_sua_la.json()["error"]["code"] == "module_not_found"


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 2: LEO THANG QUYỀN GIỮA CÁC MODULE (MODULE SCOPING ENFORCEMENT)
# ─────────────────────────────────────────────────────────────────────────────

def test_05_khach_chat_module_a_khong_goi_duoc_module_b(moi_truong):
    """Khoá khách có phạm vi chat@mod-a gọi mod-a -> 200, gọi mod-b -> 403 insufficient_scope."""
    client, so = moi_truong

    # Tạo khoá K_A có phạm vi chat@mod-a
    res_k = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách A", "pham_vi": ["chat@mod-a"]},
    )
    assert res_k.status_code == 200
    khoa_a = res_k.json()["khoa"]["khoa"]
    hdr_a = {"Authorization": f"Bearer {khoa_a}"}

    # Gọi chat model mod_a/chat-model -> 200
    res_chat_a = client.post(
        "/v1/chat/completions",
        headers=hdr_a,
        json={"model": "mod_a/chat-model", "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    assert res_chat_a.status_code == 200
    assert "Xin chào từ mod-a" in res_chat_a.json()["choices"][0]["message"]["content"]

    # Gọi chat model mod_b/chat-model -> 403 insufficient_scope
    res_chat_b = client.post(
        "/v1/chat/completions",
        headers=hdr_a,
        json={"model": "mod_b/chat-model", "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    assert res_chat_b.status_code == 403
    assert res_chat_b.json()["error"]["code"] == "insufficient_scope"

    # GET /v1/models: K_A chỉ thấy model của mod-a, KHÔNG thấy model của mod-b
    res_models = client.get("/v1/models", headers=hdr_a)
    assert res_models.status_code == 200
    ds_models = [m["id"] for m in res_models.json()["data"]]
    assert "mod_a/chat-model" in ds_models
    assert "mod_b/chat-model" not in ds_models

    # GET /v1/models/mod_b/chat-model -> 404 (giấu model ngoài phạm vi)
    res_get_b = client.get("/v1/models/mod_b/chat-model", headers=hdr_a)
    assert res_get_b.status_code == 404


def test_06_khach_chat_toan_cuc_goi_duoc_ca_hai_module(moi_truong):
    """Khoá khách có phạm vi chat (không @) gọi được cả mod-a và mod-b."""
    client, so = moi_truong

    res_k = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách Toàn Cục", "pham_vi": ["chat"]},
    )
    assert res_k.status_code == 200
    khoa_all = res_k.json()["khoa"]["khoa"]
    hdr_all = {"Authorization": f"Bearer {khoa_all}"}

    # Gọi mod-a -> 200
    res_a = client.post(
        "/v1/chat/completions",
        headers=hdr_all,
        json={"model": "mod_a/chat-model", "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    assert res_a.status_code == 200

    # Gọi mod-b -> 200
    res_b = client.post(
        "/v1/chat/completions",
        headers=hdr_all,
        json={"model": "mod_b/chat-model", "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    assert res_b.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 3: MODULE BỊ GỠ SAU KHI TẠO KHOÁ -> ĐÓNG QUYỀN
# ─────────────────────────────────────────────────────────────────────────────

def test_07_module_bi_go_thi_khoa_dong_quyen(moi_truong):
    """Module bị gỡ sau khi tạo khoá -> quyền chat@module đó bị đóng (403)."""
    client, so = moi_truong

    # Đăng ký module tạm mod-tam vào catalog của sổ module
    mod_tam = ModuleMockDonGian("mod-tam")
    km_tam = MagicMock()
    km_tam.tien_to = "mod_tam"
    km_tam.chuan_loi = {}
    km_tam.manifest = {"id": "mod-tam", "ten": "Module Tam", "cung_cap": ["chat"]}
    so.catalog["mod-tam"] = km_tam
    so.dang_chay["mod-tam"] = mod_tam

    # Tạo khoá với chat@mod-tam
    res_k = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách Tạm", "pham_vi": ["chat@mod-tam"]},
    )
    assert res_k.status_code == 200
    khoa_tam = res_k.json()["khoa"]["khoa"]
    hdr_tam = {"Authorization": f"Bearer {khoa_tam}"}

    # Khi module còn hoạt động -> gọi được
    res_truoc = client.post(
        "/v1/chat/completions",
        headers=hdr_tam,
        json={"model": "mod_tam/chat-model", "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    assert res_truoc.status_code == 200

    # Gỡ module mod-tam khỏi hệ thống
    so.catalog.pop("mod-tam", None)
    so.dang_chay.pop("mod-tam", None)

    # Sau khi gỡ: khop_pham_vi trả False và gọi API -> 403 hoặc 404 (đóng quyền hoàn toàn)
    assert khop_pham_vi("chat@mod-tam", "chat", module="mod-tam") is False

    res_sau = client.post(
        "/v1/chat/completions",
        headers=hdr_tam,
        json={"model": "mod_tam/chat-model", "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    assert res_sau.status_code in (403, 404)


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 4: KHOÁ «*» KHÔNG BAO GỒM KHẢ NĂNG NGUY HIỂM
# ─────────────────────────────────────────────────────────────────────────────

def test_08_khoa_sao_khong_bao_gom_kha_nang_nguy_hiem():
    """Khoá «*» chỉ khớp với khả năng đọc/suy luận an toàn; KHÔNG khớp với khả năng nguy hiểm."""
    # Khả năng an toàn -> khớp với «*»
    assert khop_pham_vi("*", "chat") is True
    assert khop_pham_vi("*", "anh") is True
    assert khop_pham_vi("*", "anh_sua") is True
    assert khop_pham_vi("*", "video") is True
    assert khop_pham_vi("*", "noi") is True
    assert khop_pham_vi("*", "nghe") is True
    assert khop_pham_vi("*", "nhung") is True
    assert khop_pham_vi("*", "xep_hang") is True
    assert khop_pham_vi("*", "tim_kiem") is True
    assert khop_pham_vi("*", "thiet_bi:doc") is True
    assert khop_pham_vi("*", "thiet_bi:su_kien_doc") is True

    # Khả năng nguy hiểm theo nguyên tắc lõi -> KHÔNG khớp với «*»
    assert khop_pham_vi("*", "thiet_bi:ghi") is False
    assert khop_pham_vi("*", "thiet_bi") is False
    assert khop_pham_vi("*", "thiet_bi:su_kien") is False
    assert khop_pham_vi("*", "trinh_duyet") is False
    assert khop_pham_vi("*", "trinh_duyet:chay") is False
    assert khop_pham_vi("*", "gui_ra") is False
    assert khop_pham_vi("*", "gui_tin") is False
    assert khop_pham_vi("*", "gui:mod_a") is False
    assert khop_pham_vi("*", "webhook") is False
    assert khop_pham_vi("*", "bat_ky:ghi") is False

    # Khả năng khai trong manifest["nguy_hiem"] của module -> KHÔNG khớp với «*»
    man_tuy_bien = {"nguy_hiem": ["chay_lenh_he_thong"]}
    assert khop_pham_vi("*", "chay_lenh_he_thong", manifest=man_tuy_bien) is False


def test_09_khoa_sao_bi_chan_khi_goi_kha_nang_nguy_hiem(moi_truong):
    """Khoá «*» gọi API khả năng nguy hiểm -> 403 insufficient_scope; cấp đích danh -> qua."""
    client, so = moi_truong

    # Tạo khoá khách «*»
    res_sao = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách Sao", "pham_vi": ["*"]},
    )
    assert res_sao.status_code == 200
    khoa_sao = res_sao.json()["khoa"]["khoa"]
    hdr_sao = {"Authorization": f"Bearer {khoa_sao}"}

    # Giả lập thiết bị trong so đúng chuẩn giao thức
    km_tb = MagicMock()
    km_tb.tien_to = "mod_tb"
    km_tb.chuan_loi = {}
    km_tb.manifest = {"id": "mod-tb", "ten": "Mod TB", "cung_cap": ["thiet_bi"]}
    so.catalog["mod-tb"] = km_tb

    class MockModTB:
        async def thuc_the(self, ngu_canh):
            return [{"id": "mod_tb.den_phong", "ten": "Đèn phòng", "loai": "cong_tac", "kha_nang": ["thiet_bi:doc", "thiet_bi:ghi"]}]
        async def trang_thai(self, id_thuc_the):
            return {"trang_thai": "tat"}
        async def hanh_dong(self, id_thuc_the, hanh_dong, tham_so, ngu_canh):
            return {"ok": True}

    so.dang_chay["mod-tb"] = MockModTB()

    # Khoá «*» gọi hành động thiết bị (thiet_bi:ghi - nguy hiểm) -> 403 insufficient_scope
    res_hd_sao = client.post(
        "/api/thiet-bi/mod_tb.den_phong/hanh-dong",
        headers=hdr_sao,
        json={"hanh_dong": "bat"},
    )
    assert res_hd_sao.status_code == 403
    assert res_hd_sao.json()["error"]["code"] == "insufficient_scope"
    assert "nguy hiểm" in res_hd_sao.json()["error"]["message"]

    # Tạo khoá cấp ĐÍCH DANH thiet_bi:ghi
    res_ghi = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách Ghi", "pham_vi": ["thiet_bi:ghi"]},
    )
    assert res_ghi.status_code == 200
    khoa_ghi = res_ghi.json()["khoa"]["khoa"]
    hdr_ghi = {"Authorization": f"Bearer {khoa_ghi}"}

    # Gọi với khoá đích danh -> qua bước kiểm tra phạm vi (không bị 403)
    res_hd_ghi = client.post(
        "/api/thiet-bi/mod_tb.den_phong/hanh-dong",
        headers=hdr_ghi,
        json={"hanh_dong": "bat"},
    )
    assert res_hd_ghi.status_code != 403


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 5: TRẦN QUYỀN USER (pham_vi_toi_da) VÀ IDOR
# ─────────────────────────────────────────────────────────────────────────────

def test_10_tran_quyen_user_va_tu_tao_khoa(moi_truong):
    """User không thể tự cấp quyền vượt trần (pham_vi_toi_da); không cấp được khoá '*' nếu trần hẹp."""
    client, so = moi_truong

    # Tạo user có trần chat@mod-a
    res_u = client.post(
        "/api/nguoi-dung",
        headers=QT,
        json={
            "ten_dang_nhap": "user_mod_a",
            "mat_khau": "MatKhau123!@#",
            "vai": "user",
            "pham_vi_toi_da": ["chat@mod-a"],
        },
    )
    assert res_u.status_code == 200
    mk = res_u.json()["mat_khau"]

    # Đăng nhập lấy cookie phiên web và token csrf
    res_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user_mod_a", "mat_khau": mk})
    assert res_dn.status_code == 200
    csrf = res_dn.json()["csrf"]
    hdr_pw = {"x-csrf-token": csrf}

    # Tự tạo khoá với chat@mod-a -> 200 thành công
    res_tao_ok = client.post(
        "/api/toi/khoa",
        headers=hdr_pw,
        json={"nhan": "Khoá của tôi", "pham_vi": ["chat@mod-a"]},
    )
    assert res_tao_ok.status_code == 200

    # Cố gắng tự cấp chat toàn cục (vượt trần vì bao gồm mọi module) -> 403 insufficient_scope
    res_vuot_chat = client.post(
        "/api/toi/khoa",
        headers=hdr_pw,
        json={"nhan": "Vượt trần chat", "pham_vi": ["chat"]},
    )
    assert res_vuot_chat.status_code == 403
    assert res_vuot_chat.json()["error"]["code"] == "insufficient_scope"

    # Cố gắng tự cấp chat@mod-b -> 403 insufficient_scope
    res_vuot_b = client.post(
        "/api/toi/khoa",
        headers=hdr_pw,
        json={"nhan": "Vượt sang mod B", "pham_vi": ["chat@mod-b"]},
    )
    assert res_vuot_b.status_code == 403

    # Cố gắng tự cấp «*» -> 403 insufficient_scope
    res_vuot_sao = client.post(
        "/api/toi/khoa",
        headers=hdr_pw,
        json={"nhan": "Vượt sao", "pham_vi": ["*"]},
    )
    assert res_vuot_sao.status_code == 403


def test_11_khoa_khach_khong_the_tu_tao_khoa(moi_truong):
    """Khoá khách (Bearer token) gọi đường tự phục vụ web /api/toi/khoa -> 401 Unauthorized."""
    client, so = moi_truong

    res_k = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách test", "pham_vi": ["chat@mod-a"]},
    )
    khoa_tho = res_k.json()["khoa"]["khoa"]

    res_toi = client.post(
        "/api/toi/khoa",
        headers={"Authorization": f"Bearer {khoa_tho}"},
        json={"nhan": "Tự đúc", "pham_vi": ["chat@mod-a"]},
    )
    assert res_toi.status_code in (401, 403)


def test_12_chong_idor_giua_cac_user(moi_truong):
    """User A không thể truy cập, sửa hoặc xoá khoá của User B qua đường tự phục vụ."""
    client, so = moi_truong

    # Tạo User A và User B
    res_ua = client.post(
        "/api/nguoi-dung",
        headers=QT,
        json={"ten_dang_nhap": "user_a", "mat_khau": "MatKhau123!@#", "vai": "user", "pham_vi_toi_da": ["chat@mod-a"]},
    )
    mk_a = res_ua.json()["mat_khau"]

    res_ub = client.post(
        "/api/nguoi-dung",
        headers=QT,
        json={"ten_dang_nhap": "user_b", "mat_khau": "MatKhau123!@#", "vai": "user", "pham_vi_toi_da": ["chat@mod-a"]},
    )
    mk_b = res_ub.json()["mat_khau"]

    # User A đăng nhập và tạo khoá
    res_dn_a = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user_a", "mat_khau": mk_a})
    csrf_a = res_dn_a.json()["csrf"]
    hdr_a = {"x-csrf-token": csrf_a}

    res_ka = client.post("/api/toi/khoa", headers=hdr_a, json={"nhan": "Khoá A", "pham_vi": ["chat@mod-a"]})
    assert res_ka.status_code == 200
    id_ka = res_ka.json()["khoa"]["id"]

    # User B đăng nhập (ghi đè cookie phiên web)
    res_dn_b = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user_b", "mat_khau": mk_b})
    csrf_b = res_dn_b.json()["csrf"]
    hdr_b = {"x-csrf-token": csrf_b}

    # User B xem danh sách khoá của mình -> không thấy khoá id_ka của User A
    res_ds = client.get("/api/toi/khoa")
    assert res_ds.status_code == 200
    ids_b = [k["id"] for k in res_ds.json().get("khoa", [])]
    assert id_ka not in ids_b

    # User B cố gắng xoá khoá của User A -> 404 (chống IDOR)
    res_idor_del = client.delete(
        f"/api/toi/khoa/{id_ka}",
        headers=hdr_b,
    )
    assert res_idor_del.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 6: DUYỆT MỌI ROUTE V1_AUTH VÀ KIỂM TRA PHẠM VI DÙNG MỘT HÀM CHUNG
# ─────────────────────────────────────────────────────────────────────────────

def test_13_nguyen_tac_duyet_moi_route_v1_auth(moi_truong):
    """Test nguyên tắc: duyệt mọi route V1_AUTH, khẳng định có nhận (khả năng, module) khi kiểm tra phạm vi."""
    # NV200: import đúng tên module đỉnh mà pytest đặt (test/ KHÔNG có __init__.py;
    # «test.test_…» luôn trỏ vào gói test của THƯ VIỆN CHUẨN nên không bao giờ thấy).
    from test_web2api_ma_tran_quyen import phan_loai_route, thu_thap_routes

    client, so = moi_truong
    routes = thu_thap_routes(app)
    routes_v1_auth = [r for r in routes if phan_loai_route(r) == "khach"]

    # Đảm bảo có ít nhất 10 route API khách
    assert len(routes_v1_auth) >= 10, f"Số route khách quá ít: {len(routes_v1_auth)}"

    # Tạo khoá chỉ có quyền trên mod-a
    res_k = client.post(
        "/api/khoa-khach",
        headers=QT,
        json={"nhan": "Khách Spy", "pham_vi": ["chat@mod-a"]},
    )
    khoa_spy = res_k.json()["khoa"]["khoa"]
    hdr_spy = {"Authorization": f"Bearer {khoa_spy}"}

    # Spy lời gọi tới kiem_tra_pham_vi để khẳng định nhận đúng (khả năng, module)
    loi_goi: list[dict[str, Any]] = []
    goc_kt = xac_thuc.kiem_tra_pham_vi

    def spy_kiem_tra_pham_vi(req, *cac_kn, **kwargs):
        loi_goi.append({
            "cac_kn": cac_kn,
            "module": kwargs.get("module"),
            "manifest": kwargs.get("manifest"),
        })
        return goc_kt(req, *cac_kn, **kwargs)

    with patch("app.tuyen.kiem_tra_pham_vi", side_effect=spy_kiem_tra_pham_vi), \
         patch("app.xac_thuc.kiem_tra_pham_vi", side_effect=spy_kiem_tra_pham_vi):
        # 1. Gọi chat completions trên mod-a -> kiem_tra_pham_vi nhận ("chat", module="mod-a")
        res_a = client.post(
            "/v1/chat/completions",
            headers=hdr_spy,
            json={"model": "mod_a/chat-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res_a.status_code == 200
        assert len(loi_goi) >= 1
        assert loi_goi[-1]["module"] == "mod-a"
        assert "chat" in loi_goi[-1]["cac_kn"]

        # 2. Gọi model retrieve trên mod-a -> kiem_tra_pham_vi nhận ("chat", module="mod-a")
        client.get("/v1/models/mod_a/chat-model", headers=hdr_spy)
        assert loi_goi[-1]["module"] == "mod-a"
        assert "chat" in loi_goi[-1]["cac_kn"]

        # 3. Gọi /v1/chat/completions với model của mod-b -> kiem_tra_pham_vi nhận module="mod-b" và chặn 403
        res_b = client.post(
            "/v1/chat/completions",
            headers=hdr_spy,
            json={"model": "mod_b/chat-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res_b.status_code == 403
        assert res_b.json()["error"]["code"] == "insufficient_scope"
        assert loi_goi[-1]["module"] == "mod-b"


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 7: KIỂM TRA ĐỘT BIẾN (MUTATIONS TEST)
# ─────────────────────────────────────────────────────────────────────────────

def test_14_dot_bien_bo_kiem_tra_module_se_do():
    """Kiểm tra đột biến: nếu bỏ qua kiểm tra module trong khop_pham_vi -> ĐỎ."""
    def _khop_pham_vi_dot_bien_bo_module(pv: str, kn: str, module: str | None = None) -> bool:
        """Đột biến giả định: không kiểm tra module (bỏ qua pv_mod != module)."""
        pv_kn, co_at, pv_mod = pv.partition("@")
        # Giả lập lỗi: bỏ qua kiểm tra module!
        return pv_kn == kn or kn.startswith(f"{pv_kn}:")

    # Giả lập: khách có quyền chat@mod-a nhưng gọi mod-b
    # Hàm đúng BẮT BUỘC phải trả False
    ket_qua_dung = khop_pham_vi("chat@mod-a", "chat", module="mod-b")
    assert ket_qua_dung is False, "Hàm đúng phải từ chối khi module không khớp!"

    # Hàm đột biến (lỗi) lại trả True -> Chứng minh test bắt được đột biến này
    ket_qua_dot_bien = _khop_pham_vi_dot_bien_bo_module("chat@mod-a", "chat", module="mod-b")
    assert ket_qua_dot_bien is True, "Hàm đột biến bỏ kiểm tra module đã bị phát hiện!"


def test_15_dot_bien_bo_loai_nguy_hiem_khoi_sao_se_do():
    """Kiểm tra đột biến: nếu khoá «*» vẫn cho phép khả năng nguy hiểm -> ĐỎ."""
    def _khop_pham_vi_dot_bien_sao_tat_ca(pv: str, kn: str) -> bool:
        """Đột biến giả định: «*» khớp với mọi khả năng mà không loại trừ nguy hiểm."""
        if pv == "*":
            return True  # Lỗi: không kiểm tra la_kha_nang_nguy_hiem!
        return False

    # Hàm đúng BẮT BUỘC phải từ chối thiet_bi:ghi khi dùng «*»
    assert khop_pham_vi("*", "thiet_bi:ghi") is False, "Khoá '*' không được phép thực hiện thiet_bi:ghi!"
    assert khop_pham_vi("*", "trinh_duyet") is False, "Khoá '*' không được phép điều khiển trình duyệt!"
    assert khop_pham_vi("*", "gui_tin") is False, "Khoá '*' không được phép gửi tin ra ngoài!"

    # Hàm đột biến lại chấp thuận -> Chứng minh test bắt được đột biến này
    assert _khop_pham_vi_dot_bien_sao_tat_ca("*", "thiet_bi:ghi") is True
    assert _khop_pham_vi_dot_bien_sao_tat_ca("*", "trinh_duyet") is True


# ─────────────────────────────────────────────────────────────────────────────
# PHẦN 8: KIỂM THỬ FAIL-CLOSED LA_KHA_NANG_NGUY_HIEM
# ─────────────────────────────────────────────────────────────────────────────

def test_16_tra_catalog_nem_loi_khoa_sao_khong_duoc_dung(moi_truong):
    """(a) Khi tra catalog ném lỗi/ngoại lệ, la_kha_nang_nguy_hiem fail-closed (trả True) và khoá '*' không khớp."""
    client, so = moi_truong

    # Giả lập điểm tiêm tra catalog ném lỗi (ví dụ I/O hỏng, bộ nhớ lỗi)
    def tra_catalog_nem_loi(mod_id: str):
        raise RuntimeError("Catalog I/O error / corrupted")

    with patch("app.xac_thuc._TRA_MANIFEST_FN", side_effect=tra_catalog_nem_loi):
        # Hàm la_kha_nang_nguy_hiem phải fail-closed: trả True
        assert la_kha_nang_nguy_hiem("tinh_nang_chua_ro", module="mod-a") is True

        # khop_pham_vi với '*' phải từ chối (không khớp)
        assert khop_pham_vi("*", "tinh_nang_chua_ro", module="mod-a") is False
        assert khop_pham_vi("*@mod-a", "tinh_nang_chua_ro", module="mod-a") is False

        # kiem_tra_pham_vi với khoá '*' phải ném 403 insufficient_scope
        with pytest.raises(xac_thuc.LoiOpenAI) as exc:
            kiem_tra_pham_vi(
                None,
                "tinh_nang_chua_ro",
                module="mod-a",
                danh_tinh={"loai": "khach", "id": "test_k", "pham_vi": ["*"]},
            )
        assert exc.value.ma == 403
        assert exc.value.code == "insufficient_scope"


def test_17_kha_nang_la_khong_co_trong_catalog_khong_khop_sao(moi_truong):
    """(b) Khả năng lạ (không có trong catalog) với '*' -> không khớp."""
    client, so = moi_truong

    # 1. Module mod-a trong catalog chỉ cung cấp ["chat"]. Khả năng "hanh_dong_la" không có trong catalog mod-a
    assert la_kha_nang_nguy_hiem("hanh_dong_la", module="mod-a") is True
    assert khop_pham_vi("*", "hanh_dong_la", module="mod-a") is False
    assert khop_pham_vi("*@mod-a", "hanh_dong_la", module="mod-a") is False

    # 2. Module hoàn toàn không tồn tại trong catalog
    assert la_kha_nang_nguy_hiem("chat", module="module-khong-co-that") is True
    assert khop_pham_vi("*", "chat", module="module-khong-co-that") is False

    # 3. Khả năng lạ không gắn với module và không nằm trong danh mục an toàn mặc định của lõi
    assert la_kha_nang_nguy_hiem("kha_nang_hoan_toan_la_chua_biet") is True
    assert khop_pham_vi("*", "kha_nang_hoan_toan_la_chua_biet") is False


def test_18_khoa_dich_danh_van_khop_dung(moi_truong):
    """(c) Khoá có phạm vi đích danh «<kn>@<mod>» vẫn khớp đúng (kể cả khả năng nguy hiểm hoặc lạ)."""
    client, so = moi_truong

    # 1. Khả năng an toàn trong catalog
    assert khop_pham_vi("chat@mod-a", "chat", module="mod-a") is True

    # 2. Khả năng nguy hiểm nhưng được cấp đích danh
    assert khop_pham_vi("thiet_bi:ghi@mod-a", "thiet_bi:ghi", module="mod-a") is True
    assert khop_pham_vi("chay_ma@mod-a", "chay_ma", module="mod-a") is True

    # 3. Khả năng lạ không có trong catalog nhưng được cấp đích danh cho mod-a
    assert khop_pham_vi("tinh_nang_la@mod-a", "tinh_nang_la", module="mod-a") is True

    # 4. Cấp đích danh mod-a nhưng gọi mod-b -> không khớp
    assert khop_pham_vi("chat@mod-a", "chat", module="mod-b") is False
    assert khop_pham_vi("tinh_nang_la@mod-a", "tinh_nang_la", module="mod-b") is False

