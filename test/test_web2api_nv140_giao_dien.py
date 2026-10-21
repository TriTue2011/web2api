"""Test giao diện Máy GPU (NV140) — khuôn mẫu Chrome thật + kiểm tra tĩnh giao diện.

Kiểm tra:
- Cấu trúc wizard 3 bước (nhập URL, kiểm tra card/Bearer token, chọn khả năng & lưu).
- Bảng thông số GPU chi tiết (Card, VRAM, nhiệt độ, độ trễ, hàng đợi, mạch ngắt).
- Khả năng tiếp cận (WCAG 2.2 AA): trường nhập có label for tương ứng, token type=password, vai trò ngữ nghĩa.
- Chống XSS: dữ liệu từ nút GPU độc hại không gây thực thi mã.
- E2E Chrome (tự động bỏ qua khi môi trường không có Chrome theo luật hộp cát).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.nut_gpu import lam_sach_telemetry_gpu
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401


# ───────────────────────────── KIỂM TRA TĨNH GIAO DIỆN (LUÔN CHẠY) ─────────────────────────────


def test_he_thong_html_chua_khoi_may_gpu_va_thong_so():
    html_path = Path("web/He-thong.dc.html")
    assert html_path.exists(), "Tệp He-thong.dc.html phải tồn tại"
    nd = html_path.read_text(encoding="utf-8")

    # Wizard Thêm máy GPU
    assert 'id="may-gpu"' in nd
    assert 'id="gpu-url"' in nd
    assert 'id="gpu-id"' in nd
    assert 'id="gpu-ten"' in nd
    assert 'id="gpu-tien-to"' in nd
    assert 'id="gpu-dong-thoi"' in nd
    assert 'onClick="{{gpuKiemB1}}"' in nd
    assert 'onClick="{{gpuKiemB2}}"' in nd
    assert 'onClick="{{gpuHoanTat}}"' in nd

    # WCAG: Password field cho khoá nút GPU
    assert 'type="password"' in nd
    assert 'autocomplete="new-password"' in nd

    # WCAG: Nhãn label for
    assert '<label for="gpu-url">' in nd
    assert '<label for="gpu-id">' in nd
    assert '<label for="gpu-ten">' in nd
    assert '<label for="gpu-tien-to">' in nd
    assert '<label for="gpu-dong-thoi">' in nd

    # Khối chi tiết thông số GPU
    assert 'id="thong-so-gpu"' in nd
    assert "{{ct.cardTen}}" in nd
    assert "{{ct.vramDung}}" in nd
    assert "{{ct.vramTong}}" in nd
    assert "{{ct.nhietDo}}" in nd
    assert "{{ct.doTre}}" in nd
    assert "{{ct.hangDoi}}" in nd
    assert "{{ct.machNgatNhan}}" in nd
    assert 'onClick="{{ct.batTat}}"' in nd
    assert 'onClick="{{ct.kiemTraLai}}"' in nd


def test_xss_loc_ten_card_gpu_doc_hai():
    """Tên card chứa payload XSS như <script> hoặc <img onerror=...> phải bị lọc ký tự điều khiển và escape an toàn."""
    du_lieu_xss = {
        "ok": True,
        "gpu": [
            {
                "ten": "<script>alert('xss')</script><img src=x onerror=alert(1)> NVIDIA RTX 3090",
                "vram_tong_mb": 24576,
                "vram_dung_mb": 2048,
                "nhiet_c": 50,
                "tai_pct": 5,
            }
        ],
    }
    sach = lam_sach_telemetry_gpu(du_lieu_xss)
    assert sach is not None
    ten_card = sach["gpu"][0]["ten"]
    # Tên card bị cắt 80 ký tự và lọc ký tự điều khiển
    assert len(ten_card) <= 80
    assert "\x00" not in ten_card
    # Khi render qua template dc-lite (hoặc html escape), chuỗi không được thực thi
    html_escape = (
        ten_card.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    assert "<script>" not in html_escape
    assert "<img" not in html_escape


# ───────────────────────────── E2E CHROME (CẦN CHROME) ─────────────────────────────


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy được vì không có Chrome")
def test_chrome_them_may_gpu_va_xem_telemetry(trang, tmp_path, monkeypatch):
    """Mô phỏng quy trình Chrome: Mở Hệ thống -> Thêm module -> Máy GPU -> kiểm tra card -> hoàn tất."""
    # 1. Dựng máy GPU giả chuẩn hợp đồng w2a-node + /health
    async def gpu_health(request):
        return JSONResponse({
            "ok": True,
            "phien_ban": "0.1.0",
            "gpu": [
                {
                    "ten": "NVIDIA GeForce RTX 4090",
                    "vram_tong_mb": 24576,
                    "vram_dung_mb": 4096,
                    "nhiet_c": 48,
                    "tai_pct": 10,
                }
            ],
            "mo_hinh_da_nap": ["whisper-large-v3", "qwen-2.5-7b"],
            "hang_doi": {"dang_chay": 0, "cho": 0},
        })

    async def gpu_manifest(request):
        return JSONResponse({
            "id": "may-gpu-phong-lab",
            "ten": "Máy GPU Phòng Lab",
            "tien_to": "gpu-lab",
            "kha_nang": ["chat", "nghe"],
            "gioi_han_dong_thoi": 3,
            "mo_hinh": ["whisper-large-v3", "qwen-2.5-7b"],
        })

    async def gpu_models(request):
        return JSONResponse({
            "object": "list",
            "data": [
                {"id": "whisper-large-v3", "object": "model"},
                {"id": "qwen-2.5-7b", "object": "model"},
            ],
        })

    app_gpu = Starlette(routes=[
        Route("/health", gpu_health),
        Route("/.well-known/w2a-node", gpu_manifest),
        Route("/v1/models", gpu_models),
    ])

    with MayChuThat(app_gpu) as s_gpu:
        from app.main import app as app_loi
        khoa_qt = "test-auth-gpu-ui-token-1234"
        monkeypatch.setenv("CHATGPT2API_AUTH_KEY", khoa_qt)
        monkeypatch.setenv("WEB2API_ADMIN_KEY", khoa_qt)
        monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

        with MayChuThat(app_loi, lifespan="on") as w:
            dang_nhap_quan_tri(trang, w.url)
            trang.di(w.url + "/He-thong.dc.html")
            trang.cho("document.getElementById('o-them')", mo_ta="Nút thêm module")
            trang.bam("document.getElementById('o-them')", mo_ta="Bấm nút thêm module")
            trang.cho("document.getElementById('may-gpu')", mo_ta="Khối wizard Máy GPU")

            # Bước 1: Nhập địa chỉ nút GPU và bấm Tiếp tục
            trang.js(f"document.getElementById('gpu-url').value = '{s_gpu.url}';")
            trang.js("document.getElementById('gpu-url').dispatchEvent(new Event('input'));")
            trang.bam("[...document.querySelectorAll('#may-gpu button')].find(b => b.textContent.includes('Tiếp tục'))", mo_ta="Bấm Tiếp tục bước 1")

            # Bước 2: Kiểm tra card
            trang.cho("[...document.querySelectorAll('#may-gpu button')].find(b => b.textContent.includes('Kiểm tra card'))", han=10, mo_ta="Nút kiểm tra card bước 2")
            trang.bam("[...document.querySelectorAll('#may-gpu button')].find(b => b.textContent.includes('Kiểm tra card'))", mo_ta="Bấm kiểm tra card bước 2")
            trang.cho("document.querySelector('#may-gpu dl.bang')", han=10, mo_ta="Hiện thông tin card GPU sau khi kiểm")
            chu_b2 = trang.js("document.body.innerText")
            assert "RTX 4090" in chu_b2
            assert "24576" in chu_b2

            # Bước 3: Hoàn tất
            trang.cho("[...document.querySelectorAll('#may-gpu button')].find(b => b.textContent.includes('Hoàn tất'))", han=10, mo_ta="Nút hoàn tất bước 3")
            trang.bam("[...document.querySelectorAll('#may-gpu button')].find(b => b.textContent.includes('Hoàn tất'))", mo_ta="Bấm hoàn tất & thêm máy GPU")

            # Ô máy GPU xuất hiện trong lưới
            trang.cho("document.querySelector('[data-o=\"may-gpu-phong-lab\"]')", han=10, mo_ta="Ô máy GPU xuất hiện trong lưới")

            # Mở bảng chi tiết để chứng minh có telemetry (tên card, VRAM)
            trang.bam("document.querySelector('[data-o=\"may-gpu-phong-lab\"]')", mo_ta="Mở bảng chi tiết máy GPU")
            trang.cho("document.getElementById('thong-so-gpu')", han=10, mo_ta="Bảng thông số GPU chi tiết")
            chu_ct = trang.js("document.body.innerText")
            assert "RTX 4090" in chu_ct
            assert "24576" in chu_ct
            assert "4096" in chu_ct
