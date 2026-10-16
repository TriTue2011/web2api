"""NV25 — lấp 4 lỗ hổng test do NV22 tìm ra ở nhóm người dùng / phiên web (CHỈ THÊM TEST).

ND1 (app/nguoi_dung.py ~141): kiểm mật khẩu phải qua hmac.compare_digest
    (thời gian hằng số) — spy như mẫu NV19 đã làm cho app/xac_thuc.py.
ND5 (app/nguoi_dung.py ~367): trần 64 ký tự chặn TRƯỚC khi chuẩn hoá tên —
    tên rất dài không được sao chép (strip/lower) và không ghim khoá khổng lồ
    vào _THEO_DOI_SAI.
PW2 (app/phien_web.py ~249): kiểm CSRF phải qua hmac.compare_digest — spy.
PW4 (app/phien_web.py ~171): hết hạn TUYỆT ĐỐI của phiên web — phiên có
    het_han_tuyet_doi trong quá khứ (dù dung_cuoi còn mới) bị từ chối và thu hồi.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request

from app import nguoi_dung, phien_web
from app.loi_openai import LoiOpenAI

MAT_KHAU_DUNG = "mat-khau-dung-nv25-gia"
MAT_KHAU_SAI = "mat-khau-sai-nv25-gia"


def _spy_compare_digest(monkeypatch):
    """Thay hmac.compare_digest bằng spy ghi lại các cặp đã so (giống mẫu NV19)."""
    goi: list[tuple[bytes, bytes]] = []
    goc = hmac.compare_digest

    def spy(a, b):
        goi.append((bytes(a), bytes(b)))
        return goc(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    return goi


def test_nd1_kiem_tra_mat_khau_dung_compare_digest(monkeypatch):
    """ND1: so mật khẩu (đúng và sai) đều phải qua hmac.compare_digest.

    Đột biến NV22 (compare_digest → ==) phải làm test này ĐỎ.
    """
    chuoi_bam = asyncio.run(nguoi_dung.bam_mat_khau(MAT_KHAU_DUNG))
    bam_goc = base64.b64decode(chuoi_bam.split("$")[5])
    goi = _spy_compare_digest(monkeypatch)

    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau(MAT_KHAU_DUNG, chuoi_bam)) is True
    assert any(b == bam_goc for _, b in goi), \
        "kiểm mật khẩu ĐÚNG không qua hmac.compare_digest (kênh timing attack)"
    goi.clear()

    assert asyncio.run(nguoi_dung.kiem_tra_mat_khau(MAT_KHAU_SAI, chuoi_bam)) is False
    assert any(b == bam_goc for _, b in goi), \
        "kiểm mật khẩu SAI không qua hmac.compare_digest (kênh timing attack)"


class _ChuoiLon(str):
    """Chuỗi rất dài ghi lại có bị sao chép khi chuẩn hoá (strip/lower) hay không."""

    def __new__(cls, s):
        o = super().__new__(cls, s)
        o.da_sao_chep = False
        return o

    def strip(self, *a, **k):
        self.da_sao_chep = True
        return super().strip(*a, **k)

    def lower(self):
        self.da_sao_chep = True
        return super().lower()


def test_nd5_tran_64_ky_tu_truoc_khi_chuan_hoa_ten_theo_doi():
    """ND5: tên rất dài bị chặn TRƯỚC khi sao chép, không ghim khoá khổng lồ.

    Đột biến NV22 (bỏ trần 64 ký tự ở ten_co_the_ton_tai) phải làm test này ĐỎ:
    khi đó strip()/lower() sẽ sao chép toàn bộ chuỗi khổng lồ (DoS CPU/RAM).
    """
    cuu = dict(nguoi_dung._THEO_DOI_SAI)
    nguoi_dung._THEO_DOI_SAI.clear()
    try:
        # (a) Đảm bảo công khai: tên rất dài không ghim khoá khổng lồ vào bộ theo dõi.
        nguoi_dung.ghi_nhan_dang_nhap_sai("ten-dang-nhap-" + "x" * 5_000_000)
        for khoa in nguoi_dung._THEO_DOI_SAI:
            assert len(khoa) <= 64, f"khoá theo dõi dài {len(khoa)} ký tự — trần 64 đã mất"
        # (b) Cơ chế: trần 64 ký tự phải chặn TRƯỚC khi strip()/lower() sao chép chuỗi.
        chuoi = _ChuoiLon("y" * 5_000_000)
        assert nguoi_dung.ten_co_the_ton_tai(chuoi) is None
        assert not chuoi.da_sao_chep, \
            "tên rất dài bị sao chép khi chuẩn hoá — trần 64 ký tự đã mất (DoS)"
    finally:
        nguoi_dung._THEO_DOI_SAI.clear()
        nguoi_dung._THEO_DOI_SAI.update(cuu)


def _yeu_cau_csrf(token: str) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/khoa-khach",
        "headers": [(b"host", b"testserver"), (b"x-csrf-token", token.encode("utf-8"))],
    })


def test_pw2_kiem_tra_csrf_dung_compare_digest(monkeypatch):
    """PW2: so token CSRF (đúng và sai) đều phải qua hmac.compare_digest.

    Đột biến NV22 (compare_digest → ==) phải làm test này ĐỎ.
    """
    csrf_dung = secrets.token_urlsafe(32)
    csrf_sai = secrets.token_urlsafe(32)
    assert csrf_sai != csrf_dung
    goi = _spy_compare_digest(monkeypatch)

    phien_web.kiem_tra_csrf(_yeu_cau_csrf(csrf_dung), {"csrf": csrf_dung})  # đúng → qua
    assert any(b == csrf_dung.encode("utf-8") for _, b in goi), \
        "kiểm CSRF đúng không qua hmac.compare_digest (kênh timing attack)"
    goi.clear()

    with pytest.raises(LoiOpenAI) as exc:  # sai → 403, nhưng vẫn phải so hằng số
        phien_web.kiem_tra_csrf(_yeu_cau_csrf(csrf_sai), {"csrf": csrf_dung})
    assert exc.value.ma == 403, f"muốn 403, được {exc.value.ma}"
    assert exc.value.than["error"]["code"] == "csrf_token_invalid"
    assert any(b == csrf_dung.encode("utf-8") for _, b in goi), \
        "kiểm CSRF sai không qua hmac.compare_digest (kênh timing attack)"


def test_pw4_het_han_tuyet_doi_thu_hoi_phien(tmp_path, monkeypatch):
    """PW4: phiên hết hạn TUYỆT ĐỐI bị từ chối và thu hồi, dù dung_cuoi còn mới.

    Đột biến NV22 (bỏ kiểm het_han_tuyet_doi) phải làm test này ĐỎ — nếu không,
    phiên sống vô hạn chỉ cần dung_cuoi được làm mới liên tục.
    """
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    ban_ghi, token_tho = phien_web.tao_phien("nd-gia-nv25")
    # Đối chứng: phiên mới tạo hợp lệ (test không rỗng).
    assert phien_web.xac_thuc_token(token_tho) is not None

    bay_gio = datetime.now(timezone.utc)
    het_han = dict(ban_ghi)
    het_han["het_han_tuyet_doi"] = (bay_gio - timedelta(seconds=1)).isoformat(timespec="seconds")
    het_han["dung_cuoi"] = bay_gio.isoformat(timespec="seconds")  # vẫn «mới»
    phien_web._ghi_phien([het_han])

    assert phien_web.xac_thuc_token(token_tho) is None, \
        "phiên hết hạn tuyệt đối vẫn được chấp nhận khi dung_cuoi còn mới"
    assert phien_web._doc_phien() == [], "phiên hết hạn tuyệt đối không bị thu hồi"
