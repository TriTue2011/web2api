"""Móc thu hồi phiên phụ hỏng không được bị nuốt im lặng, và không làm các móc còn lại ngừng chạy (NV98 — hậu kiểm)."""

import logging

from app import phien_web


def test_moc_hong_ghi_canh_bao_va_moc_sau_van_chay(caplog, monkeypatch):
    monkeypatch.setattr(phien_web, "_MOC_THU_HOI", [])
    da_chay = []

    def moc_hong(uid):
        raise RuntimeError("bi-mat-khong-duoc-ghi-vao-log-12345")

    def moc_tot(uid):
        da_chay.append(uid)

    phien_web.dang_ky_moc_thu_hoi(moc_hong)
    phien_web.dang_ky_moc_thu_hoi(moc_tot)
    with caplog.at_level(logging.WARNING, logger="web2api.phien_web"):
        phien_web.thu_hoi_phien_nguoi_dung("nd-thu")
    assert da_chay == ["nd-thu"]                                  # móc sau móc hỏng vẫn chạy
    ghi = " ".join(r.getMessage() for r in caplog.records)
    assert "moc_hong" in ghi and "RuntimeError" in ghi           # tên móc + loại lỗi
    assert "bi-mat-khong-duoc-ghi-vao-log" not in ghi            # không ghi nội dung lỗi
