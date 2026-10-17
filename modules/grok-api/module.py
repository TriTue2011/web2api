"""Module Grok API — trò chuyện với các model Grok qua API chính thức của xAI.

xAI có endpoint tương thích OpenAI (``https://api.x.ai/v1``): ``GET /models``,
``POST /v1/chat/completions`` (có stream), xác thực Bearer. Module KHÔNG tự
viết giao thức như các module web: thân yêu cầu đi nguyên văn qua bộ chuyển
tiếp dùng chung của lõi (``app/chuyen_tiep.py``) — giá trị module thêm vào là:
xoay vòng nhiều khoá + cho khoá nghỉ khi bị từ chối/hết hạn mức, đổi base_url
(quy định chống SSRF qua ``app/tu_xa.py``), chuẩn hoá lỗi, danh sách model lấy
từ nhà cung cấp, kiểm sức khoẻ.

Khác module grok-web: đường này là API chính thức, KHÔNG qua Cloudflare,
không cần đăng nhập web hay cookie. Model nào dùng được là do xAI trả trong
``GET /models`` — module không viết cứng tên model.

Khoá CHỈ đi bằng header ``Authorization: Bearer``, không bao giờ vào URL hay
log/phản hồi (chỉ hiện dạng che ``…abcd``); lưu ở kho bí mật 0600 của lõi.

CHƯA KIỂM với api.x.ai thật (cần khoá xAI thật).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.chuyen_tiep import ChuyenTiepOpenAI, ds_khoa
from app.tu_xa import kiem_url_lan

#: Địa chỉ gốc mặc định của xAI (endpoint tương thích OpenAI).
BASE_MAC_DINH = "https://api.x.ai/v1"


def chuan_hoa_base_url(gia_tri) -> str:
    """Bỏ «/» cuối; thiếu hậu tố /v1 thì tự thêm (endpoint OpenAI của xAI nằm
    dưới /v1). Sai scheme/thiếu tên máy → ValueError."""
    s = str(gia_tri or "").strip().rstrip("/")
    if not s:
        return BASE_MAC_DINH
    phan = urlsplit(s)
    if phan.scheme not in ("http", "https") or not phan.hostname:
        raise ValueError(f"base_url phải bắt đầu bằng http:// hoặc https:// và có tên máy: {s!r}")
    if phan.username or phan.password:
        raise ValueError("base_url không được kèm tài khoản/mật khẩu; dùng trường api_keys")
    if not phan.path.rstrip("/").endswith("/v1"):
        s = s + "/v1"
    return s


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại bằng CẤU TRÚC (mã HTTP), không dò chữ trong thông điệp:
    401/403 = khoá sai, 404 = model không có ở khoá này, 429 = hết hạn mức,
    5xx = lỗi máy chủ. Còn lại → None để lõi dùng phân loại mặc định."""
    if ma in (401, 403):
        return "khoa_hong"
    if ma == 404:
        return "khong_co_o_khoa"
    if ma == 429:
        return "het_han_muc"
    if ma is not None and 500 <= ma <= 599:
        return "may_chu_loi"
    return None


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng chat)."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "grok-api")
        self._ct: ChuyenTiepOpenAI | None = None

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        return phan_loai_loi(ma, loi, boi_canh)

    def _tao(self, khoa: list[str], base: str) -> ChuyenTiepOpenAI:
        return ChuyenTiepOpenAI(
            ten="Grok", tien_to=self.tien_to, base_url=base, khoa=khoa,
            doi_id_model=lambda x: x,  # id của xAI dùng nguyên văn
            phan_loai_loi=self.phan_loai_loi,
        )

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        cau_hinh = cau_hinh or {}
        self._ct = self._tao(
            ds_khoa(cau_hinh.get("api_keys")),
            chuan_hoa_base_url(cau_hinh.get("base_url")),
        )

    async def _kiem_base_url(self, gia_tri) -> str:
        """Chuẩn hoá + kiểm SSRF qua app/tu_xa.py; sai → ValueError."""
        return await kiem_url_lan(chuan_hoa_base_url(gia_tri))

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng khoá bằng GET /models THẬT (rẻ, không tốn token).

        Chỉ giữ khoá gọi được; không khoá nào → ok=false. base_url ghi đè
        phải qua kiểm SSRF của lõi."""
        khoa = ds_khoa((cau_hinh or {}).get("api_keys"))
        if not khoa:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập khoá API nào (trường api_keys)."}
        try:
            base = await self._kiem_base_url((cau_hinh or {}).get("base_url"))
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        ct = self._tao(khoa, base)
        try:
            ds = (await ct.suc_khoe())["khoa"]
        finally:
            await ct.dung()
        for muc in ds:
            muc.pop("dang_nghi_den", None)
        tot = [k for k, muc in zip(khoa, ds) if muc["ok"]]
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ds,
                    "loi": f"Không khoá nào gọi được {base}/models — kiểm lại khoá xAI."}
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"api_keys": tot, "base_url": base}}

    async def chat(self, body: dict, stream: bool):
        return await self._ct.chat(body, stream)

    async def models(self) -> list[dict]:
        """Danh sách model do xAI trả (GET /models) — không viết cứng."""
        return await self._ct.models()

    async def suc_khoe(self) -> dict:
        return await self._ct.suc_khoe()

    async def dung(self) -> None:
        if self._ct is not None:
            await self._ct.dung()
            self._ct = None
