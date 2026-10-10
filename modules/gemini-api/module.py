"""Module Gemini API — chuyển tiếp THẲNG sang endpoint tương thích OpenAI chính thức của Google.

Google có sẵn `https://generativelanguage.googleapis.com/v1beta/openai/` (chat/completions có
stream, tools, ảnh; models; xác thực Bearer). Nên module KHÔNG dịch sang generateContent
gốc như services/providers/gemini_free.py của c2a (523 dòng dịch hai chiều, phải tự nhớ
thoughtSignature theo mã lời gọi). Ở đây thân yêu cầu đi nguyên văn — chữ ký suy nghĩ nằm
trong `extra_content` của tool_calls tự đi theo. Phần giá trị module thêm vào: xoay vòng khoá
+ nghỉ khi lỗi, đổi base_url (vượt chặn vùng), chuẩn hoá lỗi, danh sách model, kiểm sức khoẻ —
tất cả qua bộ chuyển tiếp dùng chung của lõi (app/chuyen_tiep.py).

Mã này chạy TRONG tiến trình lõi và được nạp từ <WEB2API_DATA>/modules/gemini-api/ (cài từ git).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.chuyen_tiep import ChuyenTiepOpenAI, ds_khoa

BASE_MAC_DINH = "https://generativelanguage.googleapis.com/v1beta/openai"
GOI_Y_CHAN_VUNG = ("máy chủ bị chặn vùng — đặt base_url qua proxy "
                   "(ví dụ Cloudflare Worker deploy/cloudflare-gemini-proxy.js)")


def chuan_hoa_base_url(gia_tri) -> str:
    """Bỏ «/» cuối; gốc proxy không có /v1beta/openai thì tự thêm (proxy chuyển nguyên đường dẫn
    sang Google — giống gemini_free tự thêm /v1beta)."""
    s = str(gia_tri or "").strip().rstrip("/")
    if not s:
        return BASE_MAC_DINH
    if urlsplit(s).scheme not in ("http", "https") or not urlsplit(s).netloc:
        raise ValueError(f"base_url phải bắt đầu bằng http:// hoặc https:// và có tên máy: {s!r}")
    if s.endswith("/v1beta/openai"):
        return s
    if s.endswith("/v1beta"):
        return s + "/openai"
    return s + "/v1beta/openai"


def goi_y_loi(ma: int, thong_diep: str) -> str | None:
    """Chặn vùng là lỗi của MÁY CHỦ, không của khoá: mọi khoá sẽ hỏng y hệt. Google trả nó bằng
    400 (đo qua proxy c2a) hoặc 403 kèm «User location is not supported».

    Khoá theo chữ «location», không theo «not supported» đơn lẻ: «not supported» còn xuất hiện
    trong 400 bình thường (model không hỗ trợ tính năng) — gợi ý proxy ở đó là chỉ sai đường."""
    t = thong_diep.lower()
    if ma in (400, 403) and "location" in t:
        return GOI_Y_CHAN_VUNG
    return None


def _bo_models(id_model: str) -> str:
    # Google trả id dạng "models/gemini-2.5-flash"; khách gọi bằng "gemini/gemini-2.5-flash".
    return id_model.removeprefix("models/")


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._ct: ChuyenTiepOpenAI | None = None

    def _tao(self, khoa: list[str], base: str) -> ChuyenTiepOpenAI:
        return ChuyenTiepOpenAI(ten="Gemini", tien_to=self.tien_to, base_url=base, khoa=khoa,
                                doi_id_model=_bo_models, goi_y_loi=goi_y_loi)

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        self._ct = self._tao(ds_khoa(cau_hinh.get("api_keys")), chuan_hoa_base_url(cau_hinh.get("base_url")))

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng khoá bằng GET /models (không tốn token). Chỉ giữ khoá hợp lệ; không khoá nào → ok=false."""
        khoa = ds_khoa(cau_hinh.get("api_keys"))
        if not khoa:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập khoá API nào (trường api_keys)."}
        try:
            base = chuan_hoa_base_url(cau_hinh.get("base_url"))
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
            loi = f"Không khoá nào gọi được {base}/models."
            if any(muc["loi"] and GOI_Y_CHAN_VUNG in muc["loi"] for muc in ds):
                loi += " " + GOI_Y_CHAN_VUNG[0].upper() + GOI_Y_CHAN_VUNG[1:] + "."
            return {"ok": False, "ket_qua_tung_khoa": ds, "loi": loi}
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"api_keys": tot, "base_url": base}}

    async def chat(self, body: dict, stream: bool):
        return await self._ct.chat(body, stream)

    async def models(self) -> list[dict]:
        return await self._ct.models()

    async def suc_khoe(self) -> dict:
        return await self._ct.suc_khoe()

    async def dung(self) -> None:
        if self._ct is not None:
            await self._ct.dung()
