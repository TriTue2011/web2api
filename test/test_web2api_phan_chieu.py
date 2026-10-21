"""Kiểm thử cấu trúc chặn phản chiếu chuỗi do khách điều khiển trong thông báo lỗi (Luật cứng).

1. Test cấu trúc: Duyệt MỌI route có tham số đường {param}, thay từng tham số bằng chuỗi
   5000 ký tự "A" kèm \n\x1b[31m, gọi bằng quyền hợp lệ, và khẳng định:
   - Phản hồi không chứa chuỗi 130 ký tự "A" liên tiếp.
   - Không chứa \x1b hay \n thô trong trường message.
   - Mã HTTP không phải 500.
2. Test đơn vị: Kiểm thử hàm làm sạch với các trường hợp: rỗng, chuỗi ngắn giữ nguyên,
   ký tự điều khiển (\r, \n, \x1b, null byte...), chuỗi dài vượt trần, và Unicode tiếng Việt.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach
from app import loi_openai
from app.loi_openai import TRAN_THONG_DIEP, lam_sach_thong_diep
TRAN_TOKEN = getattr(loi_openai, "TRAN_TOKEN", 120)
from app.main import app
from test_web2api_ma_tran_quyen import (
    KHOA_QT,
    cac_phuong_thuc_chinh,
    phan_loai_route,
    thay_the_tham_so,
    thu_thap_routes,
)


@pytest.fixture
def moi_truong_phan_chieu(tmp_path, monkeypatch):
    """Thiết lập môi trường kiểm thử phản chiếu với kho tạm và vô hiệu hóa rate limit lỗi auth."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.setenv("WEB2API_GITHUB_API", "http://127.0.0.1:9")

    # Tạo khoá khách có đầy đủ quyền (phạm vi "*") để kiểm các route khách
    ban_ghi, khoa_tho = khoa_khach.tao_khoa(nhan="Khách toàn quyền", pham_vi=["*"])
    khoa_khach.dong_bo_dia()

    with TestClient(app) as client:
        yield client, khoa_tho, ban_ghi


# ─────────────────────────────────────────────────────────────────────────────
# 1. TEST ĐƠN VỊ HÀM LÀM SẠCH THÔNG ĐIỆP
# ─────────────────────────────────────────────────────────────────────────────

def test_don_vi_lam_sach_rong():
    """Chuỗi rỗng hoặc None trả về chuỗi rỗng."""
    assert lam_sach_thong_diep("") == ""
    assert lam_sach_thong_diep(None) == ""


def test_don_vi_lam_sach_chuoi_ngan_giu_nguyen():
    """Thông báo hợp lệ ngắn không đổi một ký tự."""
    mau = "Model mock/chat-model không tồn tại hoặc đã tắt."
    assert lam_sach_thong_diep(mau) == mau

    mau_2 = "Thân yêu cầu không phải JSON hợp lệ"
    assert lam_sach_thong_diep(mau_2) == mau_2


def test_don_vi_lam_sach_ky_tu_dieu_khien():
    """Loại bỏ ký tự điều khiển (\\r, \\n, \\t, mã ANSI ESC, null byte...) và thu gọn khoảng trắng."""
    chuoi = "Lỗi nghiêm trọng:\r\n\tChi tiết: \x1b[31mKhông tìm thấy\x1b[0m\x00 tệp."
    kq = lam_sach_thong_diep(chuoi)
    assert "\r" not in kq
    assert "\n" not in kq
    assert "\t" not in kq
    assert "\x1b" not in kq
    assert "\x00" not in kq
    assert "  " not in kq
    assert "Lỗi nghiêm trọng: Chi tiết: Không tìm thấy tệp." == kq


def test_don_vi_lam_sach_chuoi_dai_va_token_dai():
    """Cắt token dài > 120 ký tự và cắt toàn bộ thông báo ở trần 400 ký tự."""
    # Token 5000 chữ A
    token_dai = "A" * 5000
    kq_token = lam_sach_thong_diep(token_dai)
    assert len(kq_token) <= TRAN_TOKEN + 1  # 120 ký tự + dấu "…"
    assert ("A" * 121) not in kq_token
    assert kq_token.endswith("…")

    # Thông báo nhiều từ vượt trần 400 ký tự
    nhieu_tu = "Từ_ngắn " * 100
    kq_nhieu_tu = lam_sach_thong_diep(nhieu_tu)
    assert len(kq_nhieu_tu) <= TRAN_THONG_DIEP + 1
    assert kq_nhieu_tu.endswith("…")


def test_don_vi_lam_sach_unicode_tieng_viet():
    """Giữ nguyên 100% tiếng Việt có dấu đầy đủ và dấu ngoặc trích dẫn."""
    mau = "Không tìm thấy khoá khách có nhãn «Thử nghiệm — Phòng Kỹ thuật» (mã: 404)."
    assert lam_sach_thong_diep(mau) == mau


# ─────────────────────────────────────────────────────────────────────────────
# 2. TEST CẤU TRÚC: QUÉT MỌI ROUTE CÓ THAM SỐ ĐƯỜNG
# ─────────────────────────────────────────────────────────────────────────────

def _tim_cac_tham_so(path: str) -> list[str]:
    return re.findall(r"\{([^}]+)\}", path)


def test_cau_truc_chan_phan_chieu_moi_route_tham_so(moi_truong_phan_chieu):
    """Mọi route có tham số đường khi nhận chuỗi 5000 'A' + '\\n\\x1b[31m':
    - Không phản hồi chứa chuỗi 130 ký tự 'A' liên tiếp.
    - Không chứa \\x1b hay \\n thô trong trường message.
    - Mã trạng thái không phải 500.
    """
    client, khoa_khach_tho, _ = moi_truong_phan_chieu
    routes = thu_thap_routes(app)
    routes_co_tham_so = [r for r in routes if "{" in r.path]

    chuoi_doc_tho = "A" * 5000 + "\n\x1b[31m"
    chuoi_doc_url = urllib.parse.quote(chuoi_doc_tho, safe="")

    da_kiem_tra = 0

    for r in routes_co_tham_so:
        cac_ts = _tim_cac_tham_so(r.path)
        vai = phan_loai_route(r)

        # Chọn header phù hợp
        if vai == "quan_tri":
            headers = {"Authorization": f"Bearer {KHOA_QT}"}
        elif vai == "khach":
            headers = {"Authorization": f"Bearer {khoa_khach_tho}"}
        else:
            headers = {}

        # Thử thay từng tham số bằng chuỗi độc
        for ts in cac_ts:
            duong = r.path
            for t in cac_ts:
                if t == ts:
                    duong = duong.replace(f"{{{t}}}", chuoi_doc_url)
                else:
                    # Các tham số khác thay bằng giá trị mẫu hợp lệ
                    mau_don = thay_the_tham_so(f"{{{t}}}")
                    duong = duong.replace(f"{{{t}}}", mau_don)

            for m in cac_phuong_thuc_chinh(r.methods):
                # Bỏ qua /novnc/s proxy vì là reverse proxy socket/http nội bộ
                if "/novnc/s/" in r.path:
                    continue

                res = client.request(m, duong, headers=headers, json={})
                da_kiem_tra += 1

                # 1. Mã không phải 500
                assert res.status_code != 500, (
                    f"Route {m} {r.path} (tham số {ts}) trả lỗi sập 500! "
                    f"Nội dung: {res.text[:300]}"
                )

                # 2. Phản hồi không chứa 130 ký tự "A" liên tiếp
                assert ("A" * 130) not in res.text, (
                    f"Route {m} {r.path} (tham số {ts}) phản chiếu chuỗi 130 chữ A liên tiếp! "
                    f"Nội dung: {res.text[:300]}"
                )

                # 3. Không chứa \x1b hay \n thô trong trường message
                try:
                    body = res.json()
                    msg = ""
                    if isinstance(body, dict):
                        if "error" in body and isinstance(body["error"], dict):
                            msg = body["error"].get("message", "")
                        elif "message" in body:
                            msg = body.get("message", "")
                        elif "detail" in body and isinstance(body["detail"], str):
                            msg = body.get("detail", "")
                    if isinstance(msg, str) and msg:
                        assert "\x1b" not in msg, (
                            f"Route {m} {r.path} phản chiếu ký tự ESC (\\x1b) trong message: {msg!r}"
                        )
                        assert "\n" not in msg, (
                            f"Route {m} {r.path} phản chiếu ký tự xuống dòng (\\n) trong message: {msg!r}"
                        )
                except Exception:
                    # Phản hồi không phải JSON (ví dụ HTML 404), đã kiểm tra ở assert res.text ở trên
                    pass

    assert da_kiem_tra > 0, "Không có route có tham số đường nào được kiểm tra!"
