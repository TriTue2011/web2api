"""Hỗ trợ test NV93: trình duyệt giả có chuyển trạng thái sau khi bấm.

`TrinhDuyetGia` (app/khoi_phuc_phien.py) là tĩnh: mỗi URL luôn trả cùng
tín hiệu/cookie. Thực tế, bấm «Đăng nhập bằng Google» làm trình duyệt chuyển
trang (sang accounts.google.com rồi về trang provider đã đăng nhập). Lớp
`TrinhDuyetChuyenDoi` mô phỏng điều đó mà KHÔNG cần Internet/Chrome:

    sau_bam = {
        "https://auth.openai.com/u/login": {
            "url": "https://chatgpt.com/",          # None = ở yên trang hiện tại
            "trang": {"tin_hieu": ["thanh_cong"],   # trạng thái mới của trang đích
                      "cookie": {"access_token": "..."}},
        },
    }

Sau `await bam(...)` trên url_nguon, trang hiện tại đổi sang `url` (nếu có) và
nhận trạng thái `trang` mới — mô phỏng chuyển hướng sau SSO. Nhật ký
`hanh_dong` vẫn ghi đủ như bản gốc.

`TrinhDuyetLoi` dùng cho test «lỗi lạ đóng cửa»: mọi thao tác điều hướng ném
ngoại lệ không ngờ — module phải trả thất bại rõ, không vỡ, không thành công
âm thầm.

CHỈ DÙNG TRONG TEST. Không dùng cho mã thật.
"""

from __future__ import annotations

from app.khoi_phuc_phien import TrinhDuyetGia


class TrinhDuyetChuyenDoi(TrinhDuyetGia):
    """TrinhDuyetGia + chuyển trạng thái sau bam (mô phỏng SSO/chuyển hướng)."""

    def __init__(self, trang: dict | None = None, sau_bam: dict | None = None):
        super().__init__(trang)
        self._sau_bam = dict(sau_bam or {})

    async def bam(self, bo_chon: str) -> None:
        await super().bam(bo_chon)  # ghi nhật ký hanh_dong như bản gốc
        chuyen = self._sau_bam.get(self._hien_tai)
        if not isinstance(chuyen, dict):
            return
        url_dich = chuyen.get("url")
        if isinstance(url_dich, str) and url_dich:
            self._hien_tai = url_dich
        trang_moi = chuyen.get("trang")
        if isinstance(trang_moi, dict):
            # Thay trạng thái trang đích (không sửa dict gốc của người gọi).
            self._trang[self._hien_tai] = {
                "tin_hieu": list(trang_moi.get("tin_hieu") or []),
                "cookie": dict(trang_moi.get("cookie") or {}),
            }


class TrinhDuyetLoi(TrinhDuyetGia):
    """Trình duyệt lỗi lạ ở chỗ quyết định: module phải ĐÓNG CỬA."""

    async def mo_trang(self, url: str) -> None:
        raise RuntimeError("loi-gia-bat-ngo-o-mo-trang")
