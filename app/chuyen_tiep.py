"""Bộ chuyển tiếp THẲNG tới một dịch vụ nói chuẩn OpenAI — dùng chung cho mọi module AI.

Đây là "bộ công cụ" lõi cấp cho module (module Gemini API, module từ xa đều dùng): module
chỉ khai điểm riêng (base_url, cách đổi tên model, gợi ý lỗi), còn phần dễ sai nằm MỘT chỗ:

- Thân yêu cầu đi NGUYÊN VĂN, chỉ đổi `model` bỏ tiền tố. Không lọc trường lạ: Gemini 3
  gắn chữ ký suy nghĩ vào `tool_calls[].extra_content.google.thought_signature`; mất nó là
  400 "Function call is missing a thought_signature" ở bước gọi công cụ thứ hai (đo thật
  06/10/2026 trên c2a: mọi lượt có gọi công cụ hỏng ở bước 2).
- Stream chuyển tiếp TỪNG DÒNG SSE ngay khi tới, không gom bộ đệm (người dùng thấy chữ
  hiện dần; HA thoại bắt đầu đọc sớm).
- Xoay vòng khoá, bỏ qua khoá đang nghỉ; chỉ đổi khoá TRƯỚC byte đầu tiên gửi khách.
  Mỗi lượt thử chốt khoá vào biến cục bộ — bài học gemini_free: property tự xoay đọc
  4 lần trong một nhánh làm khoá bị đánh dấu không phải khoá vừa lỗi (một yêu cầu từng nã
  Google 981 lượt trong ~150 s).
- Lỗi chuẩn hoá về {"error": {...}} (Google đôi khi trả MẢNG [{"error": …}]); khoá xuất hiện
  trong thông điệp lỗi bị thay bằng dạng che trước khi ra khỏi tiến trình.

Phân loại lỗi (quyết định đổi khoá hay trả ngay):
  429            → khoá nghỉ theo Retry-After (mặc định 60 s, trần 10 phút), thử khoá kế.
  401/403        → khoá hỏng (nghỉ 1 giờ), thử khoá kế — TRỪ khi module nhận ra lỗi không
                   thuộc khoá (vd chặn vùng: mọi khoá đều sẽ hỏng y hệt) → trả ngay, không đánh dấu.
  5xx/lỗi mạng   → thử thêm ĐÚNG MỘT khoá nữa (sự cố phía máy chủ, nã hết khoá chỉ tốn hạn mức).
  400/404/khác   → lỗi của yêu cầu → trả ngay, không đổi khoá.
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Callable
from urllib.parse import urlsplit

import httpx

from app.kho import che
from app.loi_openai import loai_theo_ma

NGHI_429_MAC_DINH = 60.0
NGHI_429_TRAN = 600.0
NGHI_KHOA_HONG = 3600.0

KetQuaChat = tuple[int, dict, "bytes | AsyncIterator[bytes]"]


def ds_khoa(gia_tri) -> list[str]:
    """Danh sách khoá từ list hoặc chuỗi (xuống dòng/dấu phẩy/khoảng trắng), bỏ trùng giữ thứ tự."""
    if isinstance(gia_tri, str):
        gia_tri = gia_tri.replace(",", " ").split()
    if not isinstance(gia_tri, list):
        return []
    ra: list[str] = []
    for k in gia_tri:
        k = str(k).strip()
        if k and k not in ra:
            ra.append(k)
    return ra


def doc_retry_after(gia_tri: str | None, bay_gio: float) -> float:
    """Số giây nghỉ từ Retry-After (giây hoặc ngày HTTP); thiếu/hỏng → 60; trần 600; tối thiểu 1."""
    giay = None
    if gia_tri:
        gia_tri = gia_tri.strip()
        try:
            giay = float(gia_tri)
        except ValueError:
            try:
                giay = email.utils.parsedate_to_datetime(gia_tri).timestamp() - bay_gio
            except (TypeError, ValueError):
                giay = None
    if giay is None or giay != giay:  # NaN
        return NGHI_429_MAC_DINH
    return min(max(giay, 1.0), NGHI_429_TRAN)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def tra_loi_json(ma: int, du_lieu, headers: dict | None = None) -> KetQuaChat:
    return ma, {"content-type": "application/json", **(headers or {})}, json.dumps(du_lieu, ensure_ascii=False).encode()


class ChuyenTiepOpenAI:
    #: Thời gian chờ (giây). Thuộc tính thể hiện để test thu nhỏ mà không phải ngủ thật.
    TIMEOUT_KET_NOI = 10.0
    TIMEOUT_DOC = 120.0
    TIMEOUT_DOC_STREAM = 60.0  # giữa hai khúc stream
    TIMEOUT_KIEM = 10.0
    DEM_MODELS_GIAY = 600.0

    def __init__(
        self,
        *,
        ten: str,
        tien_to: str,
        base_url: str,
        khoa: list[str],
        doi_id_model: Callable[[str], str] = lambda x: x,
        goi_y_loi: Callable[[int, str], str | None] = lambda ma, tin: None,
    ):
        self.ten = ten
        self.tien_to = tien_to
        self.base_url = base_url.rstrip("/")
        # Không khoá (dịch vụ trong LAN không đòi) = một "khe" rỗng, không gửi Authorization.
        self.khoa = list(khoa) or [""]
        self._doi_id_model = doi_id_model
        self._goi_y_loi = goi_y_loi
        self._vi_tri = 0
        self._nghi: dict[str, float] = {}
        self._dem_models: list[dict] | None = None
        self._dem_luc = 0.0
        self._khach: httpx.AsyncClient | None = None
        self._dong_ho: Callable[[], float] = time.time
        self.log = logging.getLogger(f"web2api.chuyen_tiep.{tien_to}")

    # ── tiện ích ──

    def _client(self) -> httpx.AsyncClient:
        # Tạo muộn, BÊN TRONG vòng sự kiện đang chạy: AsyncClient gắn với vòng lặp tạo ra nó.
        if self._khach is None:
            self._khach = httpx.AsyncClient(follow_redirects=False)
        return self._khach

    @staticmethod
    def _tieu_de(khoa: str) -> dict:
        return {"Authorization": f"Bearer {khoa}"} if khoa else {}

    def che_khoa(self, van_ban: str) -> str:
        for k in self.khoa:
            if k:
                van_ban = van_ban.replace(k, che(k))
        return van_ban

    def nhan_khoa(self, khoa: str) -> str:
        return che(khoa) if khoa else "(không khoá)"

    def chuan_hoa_loi(self, ma: int, noi_dung: bytes) -> dict:
        """Mọi dạng lỗi thượng nguồn → {"error": {"message", "type", "code"}}, đã che khoá."""
        try:
            j = json.loads(noi_dung)
        except ValueError:
            j = None
        if isinstance(j, list) and j and isinstance(j[0], dict):
            j = j[0]  # Google OpenAI-compat đôi khi bọc lỗi trong MẢNG
        loi = j.get("error") if isinstance(j, dict) else None
        code = ma
        if isinstance(loi, dict):
            tin = str(loi.get("message") or "")
            code = loi.get("status") or loi.get("code") or ma
        elif isinstance(loi, str):
            tin = loi
        else:
            tin = noi_dung.decode("utf-8", "replace").strip()[:500]
        tin = self.che_khoa(tin or f"{self.ten} trả HTTP {ma}")
        goi_y = self._goi_y_loi(ma, tin)
        if goi_y:
            tin = f"{tin} — {goi_y}"
        return {"error": {"message": tin, "type": loai_theo_ma(ma), "code": code}}

    # ── chat ──

    def doi_model(self, body: dict) -> dict:
        than = dict(body)  # nông: mọi trường khác giữ nguyên đối tượng, không lọc
        model = than.get("model")
        if isinstance(model, str) and model.startswith(self.tien_to + "/"):
            than["model"] = model[len(self.tien_to) + 1:]
        return than

    def _con_nghi(self, bay_gio: float) -> int:
        return max(1, int(min(self._nghi.values()) - bay_gio + 0.999)) if self._nghi else int(NGHI_429_MAC_DINH)

    async def chat(self, body: dict, stream: bool) -> KetQuaChat:
        than = self.doi_model(body)
        bay_gio = self._dong_ho()
        n = len(self.khoa)
        dau = self._vi_tri % n
        self._vi_tri = (dau + 1) % n
        ung_vien = [self.khoa[(dau + i) % n] for i in range(n)]
        ung_vien = [k for k in ung_vien if self._nghi.get(k, 0.0) <= bay_gio]
        if not ung_vien:
            con = self._con_nghi(bay_gio)
            return tra_loi_json(429, {"error": {
                "message": f"Mọi khoá của {self.ten} ({n}) đang nghỉ vì hết hạn mức; khoá sớm nhất dùng lại sau {con} s.",
                "type": "rate_limit_error", "code": "rate_limit_exceeded"}}, {"retry-after": str(con)})

        timeout = httpx.Timeout(self.TIMEOUT_DOC_STREAM if stream else self.TIMEOUT_DOC, connect=self.TIMEOUT_KET_NOI)
        url = f"{self.base_url}/chat/completions"
        may = urlsplit(url).netloc
        loi_cuoi: KetQuaChat | None = None
        da_thu_lai = False
        for khoa in ung_vien:
            khach = self._client()
            req = khach.build_request("POST", url, json=than, headers=self._tieu_de(khoa), timeout=timeout)
            try:
                r = await khach.send(req, stream=True)
            except httpx.TimeoutException as e:
                loi_cuoi = tra_loi_json(504, {"error": {"message": f"{self.ten} ({may}) không trả lời kịp: {type(e).__name__}",
                                                        "type": "api_error", "code": "timeout"}})
            except httpx.HTTPError as e:
                loi_cuoi = tra_loi_json(502, {"error": {"message": f"Không kết nối được {self.ten} ({may}): {type(e).__name__}",
                                                        "type": "api_error", "code": "upstream_unreachable"}})
            else:
                if r.status_code == 200:
                    # Chỉ chuyển content-type: httpx đã giải nén gzip, chuyển nguyên content-encoding/
                    # content-length là khách giải nén lần hai và hỏng.
                    ct = r.headers.get("content-type", "application/json")
                    if stream:
                        return 200, {"content-type": ct}, self._chuyen_dong(r)
                    try:
                        noi_dung = await r.aread()
                    finally:
                        await r.aclose()
                    return 200, {"content-type": ct}, noi_dung
                try:
                    noi_dung = await r.aread()
                finally:
                    await r.aclose()
                ma = r.status_code
                loi = self.chuan_hoa_loi(ma, noi_dung)
                loi_cuoi = tra_loi_json(ma, loi)
                tin = loi["error"]["message"]
                if ma == 429:
                    nghi = doc_retry_after(r.headers.get("retry-after"), bay_gio)
                    self._nghi[khoa] = bay_gio + nghi
                    self.log.warning("%s: khoá %s hết hạn mức (429), nghỉ %.0f s", self.ten, self.nhan_khoa(khoa), nghi)
                    continue
                if ma in (401, 403) and not self._goi_y_loi(ma, tin):
                    self._nghi[khoa] = bay_gio + NGHI_KHOA_HONG
                    self.log.warning("%s: khoá %s bị từ chối (%s), nghỉ %.0f s: %s",
                                     self.ten, self.nhan_khoa(khoa), ma, NGHI_KHOA_HONG, tin[:200])
                    continue
                if ma < 500:
                    return loi_cuoi
            # 5xx hoặc lỗi mạng: thử thêm đúng một khoá.
            if da_thu_lai:
                break
            da_thu_lai = True
        assert loi_cuoi is not None
        if loi_cuoi[0] == 429:
            loi_cuoi = (429, {**loi_cuoi[1], "retry-after": str(self._con_nghi(bay_gio))}, loi_cuoi[2])
        return loi_cuoi

    async def _chuyen_dong(self, r: httpx.Response) -> AsyncIterator[bytes]:
        try:
            async for dong in r.aiter_lines():
                yield (dong + "\n").encode("utf-8")
        except httpx.HTTPError as e:
            # Đã gửi byte cho khách → không đổi khoá giữa chừng; báo bằng một sự kiện lỗi SSE thay vì cắt câm.
            self.log.warning("%s: luồng đứt giữa chừng: %s", self.ten, type(e).__name__)
            loi = {"error": {"message": f"{self.ten}: luồng đứt giữa chừng ({type(e).__name__})",
                             "type": "api_error", "code": "stream_interrupted"}}
            yield f"data: {json.dumps(loi, ensure_ascii=False)}\n\n".encode("utf-8")
        finally:
            await r.aclose()

    # ── models / kiểm khoá ──

    async def kiem_khoa(self, khoa: str) -> dict:
        """GET /models với một khoá: {khoa (che), ok, do_tre_ms, so_model, loi}. Không gọi chat (không tốn token)."""
        muc = {"khoa": self.nhan_khoa(khoa), "ok": False, "do_tre_ms": None, "so_model": 0, "loi": None}
        bat_dau = time.monotonic()
        try:
            r = await self._client().get(f"{self.base_url}/models", headers=self._tieu_de(khoa),
                                         timeout=httpx.Timeout(self.TIMEOUT_KIEM))
        except httpx.HTTPError as e:
            muc["do_tre_ms"] = int((time.monotonic() - bat_dau) * 1000)
            muc["loi"] = f"lỗi mạng: {type(e).__name__}"
            return muc
        muc["do_tre_ms"] = int((time.monotonic() - bat_dau) * 1000)
        if r.status_code != 200:
            muc["loi"] = f"HTTP {r.status_code}: {self.chuan_hoa_loi(r.status_code, r.content)['error']['message']}"
            return muc
        try:
            data = r.json().get("data")
        except (ValueError, AttributeError):
            data = None
        if not isinstance(data, list):
            # 200 mà không phải danh sách model — thường là trang đăng nhập/proxy chặn, không phải dịch vụ thật.
            muc["loi"] = "trả 200 nhưng không phải danh sách model OpenAI"
            return muc
        muc["ok"] = True
        muc["so_model"] = len(data)
        return muc

    async def models(self) -> list[dict]:
        bay_gio = self._dong_ho()
        if self._dem_models is not None and bay_gio - self._dem_luc < self.DEM_MODELS_GIAY:
            return list(self._dem_models)
        khoa = next((k for k in self.khoa if self._nghi.get(k, 0.0) <= bay_gio), self.khoa[0])
        try:
            r = await self._client().get(f"{self.base_url}/models", headers=self._tieu_de(khoa),
                                         timeout=httpx.Timeout(self.TIMEOUT_DOC, connect=self.TIMEOUT_KET_NOI))
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}: {self.chuan_hoa_loi(r.status_code, r.content)['error']['message']}")
            data = r.json().get("data")
            if not isinstance(data, list):
                raise ValueError("phản hồi không có danh sách data")
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            ly_do = self.che_khoa(f"{type(e).__name__}: {e}")
            if self._dem_models is not None:
                self.log.warning("%s: không lấy được danh sách model (%s) — dùng bản đệm cũ %d model lấy lúc %s",
                                 self.ten, ly_do, len(self._dem_models), iso(self._dem_luc))
                return list(self._dem_models)
            self.log.warning("%s: không lấy được danh sách model (%s) và chưa có bản đệm — trả rỗng", self.ten, ly_do)
            return []
        ds = []
        for m in data:
            if isinstance(m, dict) and isinstance(m.get("id"), str):
                ds.append({**m, "id": f"{self.tien_to}/{self._doi_id_model(m['id'])}"})
        self._dem_models = ds
        self._dem_luc = bay_gio
        return list(ds)

    async def suc_khoe(self) -> dict:
        bay_gio = self._dong_ho()
        ket_qua = await asyncio.gather(*(self.kiem_khoa(k) for k in self.khoa))
        for k, muc in zip(self.khoa, ket_qua):
            den = self._nghi.get(k, 0.0)
            muc["dang_nghi_den"] = iso(den) if den > bay_gio else None
        return {"ok": any(m["ok"] for m in ket_qua), "base_url": self.base_url, "khoa": list(ket_qua)}

    async def dung(self) -> None:
        if self._khach is not None:
            khach, self._khach = self._khach, None
            await khach.aclose()
