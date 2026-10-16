"""Kiểm thử tính năng Hạn mức khoá khách và Trần chi phí (Việc F - Bảo vệ khi khoá khách lộ).

Bao gồm:
1. Tạo khoá không nêu phạm vi ⇒ mặc định ["chat"], gọi ảnh ⇒ 403 (Đột biến: DOT_BIEN_MAC_DINH_PHAM_VI).
2. Tạo khoá với ["*"] tường minh ⇒ gọi được cả chat và ảnh (Đột biến: DOT_BIEN_CHAN_PHAM_VI_SAO).
3. Trần đồng thời theo khoá mặc định hữu hạn (4 luồng/khoá) (Đột biến: DOT_BIEN_TRAN_KHACH_KHONG_DAT).
4. Trần đồng thời toàn cục /v1 mặc định 32, áp dụng cho cả quản trị (Đột biến: DOT_BIEN_BO_TRAN_TOAN_CUC_QUAN_TRI).
5. Ô đồng thời được nhả khi client ngắt kết nối giữa chừng luồng stream (Đột biến: DOT_BIEN_QUEN_NHA_O_STREAM).
6. Trường tuỳ chọn het_han: quá khứ ⇒ 401, tương lai ⇒ 200 (Đột biến: DOT_BIEN_BO_QUA_KIEM_TRA_HET_HAN).
7. Hạn hỏng trên đĩa ⇒ 401 đóng khi lỗi, không bao giờ 500 (Đột biến: DOT_BIEN_NEM_500_KHI_HAN_HONG).
8. Sao lưu và khôi phục giữ nguyên trường het_han qua kiem_tep (Đột biến: DOT_BIEN_KIEM_TEP_BO_HET_HAN).
9. Biến WEB2API_KHACH_TU_TAT_NGAY: tự tắt khoá khách không dùng quá N ngày (Đột biến: DOT_BIEN_BO_QUA_TU_TAT_NGAY).
10. Kiểm thử leo thang quyền và IDOR (Luật cứng bảo mật 13/10/2026):
    - Không khoá → 401.
    - Khoá khách gọi đường quản trị tạo/sửa khoá → 403.
    - IDOR đổi/xoá khoá của người khác → 404 (không lộ tài nguyên).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import kho, khoa_khach, nhat_ky, xac_thuc, nguoi_dung
from app.main import app
from app.so_module import SoModule
from app.loi_openai import LoiOpenAI

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT_HDR = {"Authorization": f"Bearer {KHOA_QT}"}


class ModuleMockHanMuc:
    """Module giả lập phục vụ kiểm thử chat, ảnh và stream."""

    def __init__(self, tien_to: str = "mock"):
        self.tien_to = tien_to
        self.so_luot_chat = 0
        self.so_luot_anh = 0

    async def chat(self, body: dict, stream: bool = False):
        self.so_luot_chat += 1
        if stream:
            async def _phat():
                for i in range(5):
                    chunk = {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": body.get("model", f"{self.tien_to}/chat-model"),
                        "choices": [{"index": 0, "delta": {"content": f"data {i} "}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                    await asyncio.sleep(0.05)
                yield b"data: [DONE]\n\n"
            return 200, {"content-type": "text/event-stream"}, _phat()

        tra_ve = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", f"{self.tien_to}/chat-model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Xin chào"}, "finish_reason": "stop"}],
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")

    async def anh(self, body: dict):
        self.so_luot_anh += 1
        tra_ve = {
            "created": int(time.time()),
            "data": [{"url": "http://example.com/anh.png"}],
        }
        return 200, {"content-type": "application/json"}, json.dumps(tra_ve).encode("utf-8")

    async def models(self) -> list[dict]:
        return [
            {"id": f"{self.tien_to}/chat-model", "object": "model", "kha_nang": ["chat"]},
            {"id": f"{self.tien_to}/image-model", "object": "model", "kha_nang": ["anh"]},
        ]

    async def suc_khoe(self) -> dict:
        return {"ok": True, "khoa": [{"khoa": "…mock", "ok": True}]}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Tắt rate limit sai khoá để tránh 429 phụ trong bộ kiểm
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")

    mod = ModuleMockHanMuc("mock")
    so = SoModule()
    so.tu_xa["mock"] = {"tien_to": "mock", "url": "http://mock"}
    so.dang_chay["mock"] = mod

    with TestClient(app) as client:
        client.app.state.so = so
        yield client, mod, data_dir


# ─────────────────────────────────────────────────────────────────────────────
# 1. PHẠM VI MẶC ĐỊNH LÀ CHAT (KHÔNG CÒN "*")
# ─────────────────────────────────────────────────────────────────────────────

def test_tao_khoa_mac_dinh_chat_khong_goi_duoc_anh(moi_truong):
    """(a) Tạo khoá không nêu pham_vi ⇒ mặc định ["chat"], gọi ảnh ⇒ 403 insufficient_scope.
    Đột biến: DOT_BIEN_MAC_DINH_PHAM_VI (nếu đổi mặc định về ["*"] thì gọi ảnh được 200 và test đỏ)."""
    client, _, _ = moi_truong

    # Tạo khoá không gửi trường pham_vi
    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "Khoá mặc định"})
    assert r.status_code == 200
    khoa_info = r.json()["khoa"]
    k_tho = khoa_info["khoa"]

    # Kiểm tra phạm vi mặc định là ["chat"]
    assert khoa_info["pham_vi"] == ["chat"]
    hd_khach = {"Authorization": f"Bearer {k_tho}"}

    # Gọi chat completions thành công
    r_chat = client.post(
        "/v1/chat/completions",
        headers=hd_khach,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_chat.status_code == 200

    # Gọi tạo ảnh bị từ chối 403 insufficient_scope
    r_anh = client.post(
        "/v1/images/generations",
        headers=hd_khach,
        json={"model": "mock/image-model", "prompt": "Vẽ mèo"},
    )
    assert r_anh.status_code == 403
    assert r_anh.json()["error"]["code"] == "insufficient_scope"


def test_tao_khoa_pham_vi_sao_tuong_minh_goi_duoc_moi_thu(moi_truong):
    """(a) ["*"] vẫn chọn được NHƯNG phải nêu tường minh.
    Đột biến: DOT_BIEN_CHAN_PHAM_VI_SAO (nếu cấm ["*"] thì gọi ảnh trả 403 và test đỏ)."""
    client, _, _ = moi_truong

    # Tạo khoá với pham_vi: ["*"] tường minh
    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "Khoá toàn quyền", "pham_vi": ["*"]})
    assert r.status_code == 200
    khoa_info = r.json()["khoa"]
    k_tho = khoa_info["khoa"]
    assert khoa_info["pham_vi"] == ["*"]

    hd_khach = {"Authorization": f"Bearer {k_tho}"}

    # Gọi chat thành công
    r_chat = client.post(
        "/v1/chat/completions",
        headers=hd_khach,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_chat.status_code == 200

    # Gọi tạo ảnh cũng thành công
    r_anh = client.post(
        "/v1/images/generations",
        headers=hd_khach,
        json={"model": "mock/image-model", "prompt": "Vẽ hoa"},
    )
    assert r_anh.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 2. TRẦN ĐỒNG THỜI THEO KHOÁ VÀ TOÀN CỤC /v1
# ─────────────────────────────────────────────────────────────────────────────

def test_tran_dong_thoi_theo_khoa_mac_dinh_huu_han(moi_truong, monkeypatch):
    """(b) tran_dong_thoi() mặc định hữu hạn (4 luồng/khoá); 0 = tắt tường minh.
    Đột biến: DOT_BIEN_TRAN_KHACH_KHONG_DAT (nếu tran_dong_thoi() trả None khi chưa đặt env thì yêu cầu thứ 5 lọt qua và test đỏ)."""
    client, _, _ = moi_truong
    # Không đặt WEB2API_KHACH_DONG_THOI
    monkeypatch.delenv("WEB2API_KHACH_DONG_THOI", raising=False)

    assert khoa_khach.tran_dong_thoi() == 4

    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "Khách 4 luồng", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]
    hd = {"Authorization": f"Bearer {k_tho}"}

    # Chiếm trước 4 slot đồng thời của khoá này
    for _ in range(4):
        khoa_khach.vao_dong_thoi(id_k)

    try:
        # Lượt thứ 5 gửi đến phải bị 429
        r5 = client.post(
            "/v1/chat/completions",
            headers=hd,
            json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Quá trần"}]},
        )
        assert r5.status_code == 429
        assert r5.headers.get("retry-after") == "1"
        assert r5.json()["error"]["code"] == "rate_limit_exceeded"
    finally:
        # Giải phóng các slot đã chiếm
        for _ in range(4):
            khoa_khach.ra_dong_thoi(id_k)

    # Sau khi giải phóng, gửi tiếp thành công
    r_ok = client.post(
        "/v1/chat/completions",
        headers=hd,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Được rồi"}]},
    )
    assert r_ok.status_code == 200


def test_tran_dong_thoi_toan_cuc_v1_va_ap_cho_quan_tri(moi_truong, monkeypatch):
    """(b) Trần đồng thời TOÀN CỤC cho /v1 (WEB2API_V1_DONG_THOI_TOAN_CUC, mặc định 32; đầy ⇒ 429 + Retry-After).
    Khoá quản trị cũng chịu trần toàn cục nhưng KHÔNG chịu trần theo khoá.
    Đột biến: DOT_BIEN_BO_TRAN_TOAN_CUC_QUAN_TRI (nếu quản trị không chịu trần toàn cục thì khi đầy vẫn 200, test đỏ)."""
    client, _, _ = moi_truong
    monkeypatch.delenv("WEB2API_V1_DONG_THOI_TOAN_CUC", raising=False)

    assert khoa_khach.tran_dong_thoi_toan_cuc() == 32

    # Chiếm 32 slot toàn cục
    for _ in range(32):
        khoa_khach.vao_dong_thoi_toan_cuc()

    try:
        # 1. Quản trị gọi /v1/chat/completions khi toàn cục đầy ⇒ 429
        r_qt = client.post(
            "/v1/chat/completions",
            headers=QT_HDR,
            json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "QT gọi"}]},
        )
        assert r_qt.status_code == 429
        assert r_qt.headers.get("retry-after") == "1"

        # 2. Khách gọi /v1/chat/completions khi toàn cục đầy ⇒ 429
        r_k = client.post(
            "/api/khoa-khach",
            headers=QT_HDR,
            json={"nhan": "K1", "pham_vi": ["chat"]},
        )
        # Đường /api/khoa-khach là /api/* (không thuộc /v1) nên không chịu trần toàn cục /v1
        assert r_k.status_code == 200
        k_tho = r_k.json()["khoa"]["khoa"]

        r_k_v1 = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {k_tho}"},
            json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Khách gọi"}]},
        )
        assert r_k_v1.status_code == 429
    finally:
        for _ in range(32):
            khoa_khach.ra_dong_thoi_toan_cuc()

    # Sau khi giải phóng, quản trị gọi /v1 thành công
    r_qt_ok = client.post(
        "/v1/chat/completions",
        headers=QT_HDR,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Hết đầy"}]},
    )
    assert r_qt_ok.status_code == 200


def test_nha_o_dong_thoi_khi_client_ngat_stream(moi_truong):
    """(b) Mọi luồng stream phải nhả ô khi kết thúc/ngắt (có test cho ngắt giữa chừng).
    Đột biến: DOT_BIEN_QUEN_NHA_O_STREAM (nếu không nhả ở finally của MiddlewareNhatKyCuocGoi thì ô bị kẹt và test đỏ)."""
    client, _, _ = moi_truong

    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "K_Stream", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]
    hd = {"Authorization": f"Bearer {k_tho}"}

    # Bắt đầu stream và chỉ đọc 1 chunk rồi đóng stream (ngắt giữa chừng)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=hd,
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Stream"}], "stream": True},
    ) as stream_res:
        assert stream_res.status_code == 200
        for chunk in stream_res.iter_lines():
            if chunk:
                break  # Ngắt ngay sau byte/dòng đầu tiên

    # Sau khi ra khỏi with (client đóng kết nối), ô đồng thời của khách và toàn cục phải về 0
    assert khoa_khach._DONG_THOI_DANG_CHAY.get(id_k, 0) == 0
    assert khoa_khach._V1_DONG_THOI_HIEN_TAI == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. TRƯỜNG TUỲ CHỌN HET_HAN VÀ TỰ TẮT KHI LÂU KHÔNG DÙNG
# ─────────────────────────────────────────────────────────────────────────────

def test_het_han_khoa_khach_qua_khu_401_tuong_lai_200(moi_truong):
    """(c) Khoá khách có trường tuỳ chọn het_han (ISO, UTC): quá hạn ⇒ 401 như khoá bị tắt; tương lai ⇒ 200.
    Đột biến: DOT_BIEN_BO_QUA_KIEM_TRA_HET_HAN (nếu không kiểm tra het_han thì khoá quá hạn vẫn trả 200, test đỏ)."""
    client, _, _ = moi_truong

    # 1. Khoá có hạn ở tương lai (+2 ngày)
    han_tuong_lai = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = client.post(
        "/api/khoa-khach",
        headers=QT_HDR,
        json={"nhan": "Khoá tương lai", "pham_vi": ["chat"], "het_han": han_tuong_lai},
    )
    assert r.status_code == 200
    k_info = r.json()["khoa"]
    k_tho = k_info["khoa"]
    id_k = k_info["id"]
    assert k_info["het_han"] == han_tuong_lai

    # Gọi thành công 200
    r_ok = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {k_tho}"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_ok.status_code == 200

    # 2. Sửa hạn thành quá khứ (-1 ngày)
    han_qua_khu = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r_sua = client.patch(f"/api/khoa-khach/{id_k}", headers=QT_HDR, json={"het_han": han_qua_khu})
    assert r_sua.status_code == 200
    assert r_sua.json()["khoa"]["het_han"] == han_qua_khu

    # Gọi bị từ chối 401 như khoá bị tắt
    r_het_han = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {k_tho}"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_het_han.status_code == 401
    assert r_het_han.json()["error"]["code"] == "invalid_api_key"


def test_han_hong_tren_dia_dong_khi_loi_401_khong_500(moi_truong):
    """(c) Tra cứu theo thời gian phải đóng khi lỗi (trường hỏng ⇒ coi như hết hạn ⇒ 401, không 500).
    Đột biến: DOT_BIEN_NEM_500_KHI_HAN_HONG (nếu ném lỗi định dạng thì ra 500, test đỏ)."""
    client, _, data_dir = moi_truong

    # Tạo một khoá bình thường
    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "Khoá sẽ hỏng", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]

    # Sửa trực tiếp tệp trên đĩa đưa giá trị het_han không phải ISO
    p = data_dir / "khoa_khach.json"
    du_lieu = kho.doc_json(p)
    for item in du_lieu["khoa"]:
        if item["id"] == id_k:
            item["het_han"] = "khong-phai-thoi-gian-iso"
            break
    kho.ghi_nguyen_tu(p, json.dumps(du_lieu), quyen=0o600)
    khoa_khach.lam_moi_cache()

    # Gọi /v1/chat/completions: đóng khi lỗi → 401 (như khoá hết hạn), KHÔNG 500
    r_call = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {k_tho}"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_call.status_code == 401
    assert r_call.json()["error"]["code"] == "invalid_api_key"


def test_sao_luu_khoi_phuc_giu_nguyen_het_han(moi_truong):
    """(c) Kiểm tệp (kiem_tep) phải nhận/kiểm trường mới nên sao lưu/khôi phục không làm mất hạn.
    Đột biến: DOT_BIEN_KIEM_TEP_BO_HET_HAN (nếu kiem_tep bỏ qua het_han thì sau khôi phục khoá mất hạn, test đỏ)."""
    client, _, data_dir = moi_truong

    han_iso = "2028-12-31T23:59:59+00:00"
    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "K_HSD", "pham_vi": ["chat"], "het_han": han_iso})
    assert r.status_code == 200

    # Đọc dữ liệu từ đĩa và kiểm tra qua kiem_tep
    p = data_dir / "khoa_khach.json"
    du_lieu = kho.doc_json(p)
    chuan_hoa = khoa_khach.kiem_tep(du_lieu)
    assert len(chuan_hoa) == 1
    assert chuan_hoa[0]["het_han"] == han_iso

    # Danh sách khoá cũng phải hiện trường het_han
    r_ds = client.get("/api/khoa-khach", headers=QT_HDR)
    assert r_ds.status_code == 200
    ds = r_ds.json()["khoa"]
    assert any(k.get("het_han") == han_iso for k in ds)


def test_tu_tat_khoa_khach_khong_dung_qua_n_ngay(moi_truong, monkeypatch):
    """(c) Biến WEB2API_KHACH_TU_TAT_NGAY (>0 ⇒ khoá khách không dùng quá N ngày bị coi như tắt khi tra cứu, dựa dung_cuoi).
    Đột biến: DOT_BIEN_BO_QUA_TU_TAT_NGAY (nếu không kiểm tra biến này thì khoá để lâu vẫn dùng được, test đỏ)."""
    client, _, data_dir = moi_truong
    monkeypatch.setenv("WEB2API_KHACH_TU_TAT_NGAY", "30")

    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "K_Cu", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    id_k = r.json()["khoa"]["id"]

    # Ghi nhận lần dùng cuối là 40 ngày trước
    bon_muoi_ngay_truoc = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    p = data_dir / "khoa_khach.json"
    du_lieu = kho.doc_json(p)
    for item in du_lieu["khoa"]:
        if item["id"] == id_k:
            item["dung_cuoi"] = bon_muoi_ngay_truoc
            break
    kho.ghi_nguyen_tu(p, json.dumps(du_lieu), quyen=0o600)
    khoa_khach.lam_moi_cache()

    # Đã quá 30 ngày không dùng ⇒ bị coi như tắt (401)
    r_call = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {k_tho}"},
        json={"model": "mock/chat-model", "messages": [{"role": "user", "content": "Alo"}]},
    )
    assert r_call.status_code == 401
    assert r_call.json()["error"]["code"] == "invalid_api_key"


# ─────────────────────────────────────────────────────────────────────────────
# 4. BẢO MẬT: KIỂM LEO THANG QUYỀN VÀ IDOR (LUẬT CỨNG BẢO MẬT)
# ─────────────────────────────────────────────────────────────────────────────

def test_bao_mat_leo_thang_quyen_khong_khoa_401(moi_truong):
    """Bảo mật: Mọi đường có xác thực khi không có khoá ⇒ 401."""
    client, _, _ = moi_truong

    # Không có Authorization header
    assert client.get("/v1/models").status_code == 401
    assert client.post("/v1/chat/completions", json={"model": "mock/chat-model"}).status_code == 401
    assert client.get("/api/khoa-khach").status_code == 401
    assert client.post("/api/khoa-khach", json={"nhan": "hack"}).status_code == 401


def test_bao_mat_leo_thang_quyen_khoa_khach_goi_quan_tri_403(moi_truong):
    """Bảo mật: Khoá khách / vai thấp gọi đường quản trị ⇒ 403."""
    client, _, _ = moi_truong

    r = client.post("/api/khoa-khach", headers=QT_HDR, json={"nhan": "Khách thường", "pham_vi": ["chat"]})
    k_tho = r.json()["khoa"]["khoa"]
    hd_khach = {"Authorization": f"Bearer {k_tho}"}

    # Cố tình gọi API quản trị khoá khách
    assert client.get("/api/khoa-khach", headers=hd_khach).status_code == 403
    assert client.post("/api/khoa-khach", headers=hd_khach, json={"nhan": "hack"}).status_code == 403
    assert client.patch("/api/khoa-khach/kk_bat_ky", headers=hd_khach, json={"bat": True}).status_code == 403
    assert client.delete("/api/khoa-khach/kk_bat_ky", headers=hd_khach).status_code == 403


def test_bao_mat_idor_khoa_khach_nguoi_dung_khac_404(moi_truong):
    """Bảo mật: Đụng tài nguyên của người dùng khác (IDOR) qua /api/toi/khoa/{id} ⇒ 404 không lộ thông tin."""
    client, _, _ = moi_truong

    # Tạo 2 người dùng user1 và user2
    u1, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user1", mat_khau="MatKhauTest123!"))
    u2, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user2", mat_khau="MatKhauTest123!"))

    # Tạo khoá cho user2
    k2, _ = khoa_khach.tao_khoa(nhan="Khoá của U2", pham_vi=["chat"], chu_id=u2["id"])

    # Đăng nhập bằng user1
    r_dn = client.post("/api/dang-nhap", json={"ten_dang_nhap": "user1", "mat_khau": "MatKhauTest123!"})
    assert r_dn.status_code == 200
    csrf = r_dn.json()["csrf"]

    # user1 cố tình xoá khoá của user2 qua IDOR
    r_xoa = client.delete(f"/api/toi/khoa/{k2['id']}", headers={"X-CSRF-Token": csrf})
    assert r_xoa.status_code == 404
    assert r_xoa.json()["error"]["code"] == "key_not_found"

    # Khoá của user2 vẫn còn nguyên
    assert any(k["id"] == k2["id"] for k in khoa_khach.danh_sach_khoa_theo_chu(u2["id"]))
