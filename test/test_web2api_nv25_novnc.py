"""NV25 — lấp lỗ hổng test do NV22 tìm ra ở nhóm novnc (CHỈ THÊM TEST, không sửa mã).

NV3 (app/novnc.py ~254): vé không đúng dạng MAU_VE phải bị trả 401 NGAY, trước khi
băm/tra cứu — khẳng định bằng spy: hàm băm không được gọi với vé sai dạng.
"""

from __future__ import annotations

import secrets

from app import novnc


def test_nv3_ve_sai_dang_401_truoc_khi_bam(monkeypatch):
    """NV3: vé sai dạng → 401 ngay, không băm/tra cứu.

    Đột biến NV22 (bỏ kiểm dạng vé MAU_VE) phải làm test này ĐỎ.
    """
    da_bam: list[str] = []
    goc = novnc._bam

    def spy(s: str) -> str:
        da_bam.append(s)
        return goc(s)

    monkeypatch.setattr(novnc, "_bam", spy)

    # Vé chứa ký tự lạ, không khớp MAU_VE ([A-Za-z0-9_-]{20,128}).
    ma, phien, tt = novnc.doi_ve_lay_phien("ve!!khong_hop_le!!")
    assert ma == 401, f"vé sai dạng phải 401, được {ma}"
    assert phien is None and tt is None
    assert da_bam == [], f"vé sai dạng đã bị băm/tra cứu trước khi trả 401: {da_bam}"

    # Đối chứng: vé ĐÚNG dạng (nhưng không tồn tại) vẫn đi qua bước băm → 401 sau.
    ma2, _, _ = novnc.doi_ve_lay_phien(secrets.token_urlsafe(32))
    assert ma2 == 401
    assert len(da_bam) == 1, "spy không quan sát được bước băm của vé đúng dạng"
