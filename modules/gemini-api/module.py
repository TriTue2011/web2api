"""Module Gemini API — chat và tạo ẢNH qua endpoint tương thích OpenAI của Google; tạo VIDEO qua API gốc Veo.

Google có sẵn `https://generativelanguage.googleapis.com/v1beta/openai/` (chat/completions có
stream, tools, ảnh; models; images/generations; xác thực Bearer). Nên module KHÔNG dịch sang generateContent
gốc như services/providers/gemini_free.py của c2a (523 dòng dịch hai chiều, phải tự nhớ
thoughtSignature theo mã lời gọi). Ở đây thân yêu cầu đi nguyên văn — chữ ký suy nghĩ nằm
trong `extra_content` của tool_calls tự đi theo. Phần giá trị module thêm vào: xoay vòng khoá
+ nghỉ khi lỗi, đổi base_url (vượt chặn vùng), chuẩn hoá lỗi, danh sách model, kiểm sức khoẻ —
tất cả qua bộ chuyển tiếp dùng chung của lõi (app/chuyen_tiep.py).

VIDEO đi API GỐC (`:predictLongRunning`), không đi `/v1beta/openai/videos` tương thích Sora: trang tương thích
(đọc 11/10/2026) chỉ ghi tạo + hỏi trạng thái, KHÔNG ghi cách tải tệp video, và ví dụ gửi multipart; trang Veo
ghi đủ cả vòng (tạo → hỏi thao tác tới `done` → URI tải bằng khoá ở header). Module dịch sang/từ dạng video
OpenAI (`queued`/`in_progress`/`completed`/`failed`). Gemini Omni (Interactions API) CHƯA hỗ trợ.

ẢNH đi `/images/generations` của đường tương thích: trang tương thích (đọc 12/10/2026) ghi «Generate an image using
gemini-2.5-flash-image or gemini-3-pro-image-preview» — tức chính model ảnh dạng Gemini; trang không còn nhắc Imagen,
nên Imagen qua đường này là CHƯA ghi trong tài liệu, chưa đo. Nhóm model ảnh trong `models()`: xem `_kha_nang`.

Ảnh/video KHÔNG có ở hạng miễn phí của Google («Not available» trên trang giá, đọc 11/10/2026): khoá bị từ chối
được nhớ theo (khoá, khả năng) và không ảnh hưởng chat — xem `goi_kha_nang` trong app/chuyen_tiep.py.

Khoá CHỈ đi bằng header (Bearer cho đường tương thích, `x-goog-api-key` cho API gốc), không bao giờ vào URL,
và chỉ gửi tới máy của base_url — tải video theo chuyển hướng sang máy khác thì bỏ khoá.

Mã này chạy TRONG tiến trình lõi và được nạp từ <WEB2API_DATA>/modules/gemini-api/ (cài từ git).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx

from app.chuyen_tiep import (
    ChuyenTiepOpenAI,
    LoiDichBiChan,
    LuongNhiPhan,
    PhanHoiQuaLon,
    TransportGhimIp,
    doc_phan_hoi_co_tran,
    ds_khoa,
    tra_loi_json,
)
from app.tu_xa import _dia_chi_bi_chan, cho_phep_loopback_cho_test


def _kiem_ip_tai_video(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        if isinstance(ip, ipaddress.IPv6Address):
            return "IPv6 loopback"
        return None if cho_phep_loopback_cho_test() else "loopback (chính container)"
    if ip.is_private:
        return "mạng riêng tư (private IP)"
    return _dia_chi_bi_chan(ip)


BASE_MAC_DINH = "https://generativelanguage.googleapis.com/v1beta/openai"
GOI_Y_HANG_MIEN_PHI = ("Hạng miễn phí của Google không dùng được tính năng này (trang giá ghi «Not available») — "
                       "cần khoá thuộc dự án đã bật thanh toán (trả phí).")
GOI_Y_CHAN_VUNG = ("máy chủ bị chặn vùng — đặt base_url qua proxy "
                   "(ví dụ Cloudflare Worker deploy/cloudflare-gemini-proxy.js)")
#: Tên model đi vào ĐƯỜNG DẪN của API gốc: bắt đầu bằng chữ/số, không «/» «:» «..» — chặn chế đường.
TEN_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
#: Tên thao tác Veo giải mã từ mã video của KHÁCH: phải đúng dạng này trước khi gọi Google kèm khoá.
TEN_THAO_TAC = re.compile(r"^models/[A-Za-z0-9][A-Za-z0-9._-]{0,99}/operations/[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
MA_VIDEO = re.compile(r"^([A-Za-z0-9_-]{1,600})\.([0-9a-f]{8})$")
#: Trường của thân /v1/videos module tự dịch; mọi trường KHÁC đi vào `parameters` của Veo theo quy tắc
#: snake_case → camelCase (aspect_ratio → aspectRatio, negative_prompt → negativePrompt…) — không giữ bảng tham số.
_TRUONG_TU_DICH = ("model", "prompt", "image", "input_reference", "last_frame", "seconds", "size")
CHUYEN_HUONG_TOI_DA = 5


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


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Hook phân loại lỗi của Gemini:
    - Chặn vùng là lỗi của MÁY CHỦ, không của khoá: mọi khoá sẽ hỏng y hệt. Google trả nó bằng
      400 (đo qua proxy c2a) hoặc 403 kèm «User location is not supported».
      Khoá theo chữ «location», không theo «not supported» đơn lẻ: «not supported» còn xuất hiện
      trong 400 bình thường (model không hỗ trợ tính năng) — gợi ý proxy ở đó là chỉ sai đường.
      Bổ sung gợi ý proxy và trả 'chan_vung'.
    - Còn lại: trả None để lõi dùng phân loại mặc định (429->het_han_muc, 401/403->khoa_hong, 5xx->may_chu_loi, còn lại->khong_ro).
    """
    msg = ""
    if isinstance(loi, dict) and isinstance(loi.get("error"), dict):
        msg = str(loi["error"].get("message") or "")

    if ma in (400, 403) and "location" in msg.lower():
        if GOI_Y_CHAN_VUNG not in msg and isinstance(loi, dict) and "error" in loi:
            loi["error"]["message"] = f"{msg} — {GOI_Y_CHAN_VUNG}".strip()
        return "chan_vung"

    return None


def _bo_models(id_model: str) -> str:
    # Google trả id dạng "models/gemini-2.5-flash"; khách gọi bằng "gemini/gemini-2.5-flash".
    return id_model.removeprefix("models/")


def _kha_nang(ten: str, phuong_thuc: list) -> list[str]:
    """Khả năng của một model trong danh sách API gốc → nhãn `kha_nang` ([] = model chat, không gắn).

    - "video": có `predictLongRunning` (Veo) — dấu hiệu CẤU TRÚC.
    - "anh":   có `predict` (Imagen) — dấu hiệu CẤU TRÚC; HOẶC có `generateContent` và id (tách thành ĐOẠN theo
               «-» «_» «.» «/») mang tên của Google cho model ảnh:
                 (1) một đoạn đúng bằng «image» (gemini-3.1-flash-image, gemini-3-pro-image-preview…);
                 (2) hai đoạn liền nhau «nano» «banana» — tên thương hiệu Google đặt cho dòng model ảnh Gemini
                     (gemini-nano-banana-2.1 — model ảnh Google khuyên cho dự án mới —, nano-banana-pro-preview).

    (1) và (2) là hai chỗ DUY NHẤT của module phân loại theo chữ, và là cố ý: model ảnh dạng Gemini có
    `supportedGenerationMethods` GIỐNG HỆT model chat (generateContent, countTokens, batchGenerateContent — đo
    12/10/2026 trên 62 model thật), và cả models.list lẫn trang tạo ảnh của Google đều không nêu trường nào cho biết
    kiểu đầu ra, nên không nguyên tắc cấu trúc nào phân biệt được. Còn lại tên do chính Google đặt. Nó nằm TRONG
    module vì là đặc thù nhà cung cấp; lõi chỉ đọc `kha_nang`. (2) có vì trang tạo ảnh ghi mọi model ảnh mang hậu tố
    «image» TRỪ gemini-nano-banana-2.1 — thiếu (2) thì model được khuyên dùng không có trong «Vẽ ảnh». Đo trên 46 id
    thật (12/10/2026): (1) bắt đúng 6 model ảnh, (2) bắt đúng 2 model Nano Banana, không dương tính giả.
    Khớp ĐOẠN nguyên, không chuỗi con («imagery», «nanobanana» không tính), và KHÔNG đọc displayName/description:
    model chat ghi «image» trong mô tả như một loại ĐẦU VÀO → dương tính giả, và không có dữ liệu thật để đo.
    Model ảnh mới mang tên khác cả hai quy ước sẽ KHÔNG được gắn (yêu cầu gọi nó theo tên vẫn được chuyển đi)."""
    kn = []
    doan = re.split(r"[-_./]", ten.lower())
    ten_anh = "image" in doan or any(doan[i:i + 2] == ["nano", "banana"] for i in range(len(doan) - 1))
    if "predict" in phuong_thuc or ("generateContent" in phuong_thuc and ten_anh):
        kn.append("anh")
    if "predictLongRunning" in phuong_thuc:
        kn.append("video")
    return kn


def _goc(url: str) -> tuple[str, str]:
    p = urlsplit(url)
    return p.scheme, p.netloc


def _van_tay(khoa: str) -> str:
    """8 ký tự hex đầu của sha256(khoá): đủ chọn lại đúng khoá đã tạo video, không suy ngược được khoá."""
    return hashlib.sha256(khoa.encode()).hexdigest()[:8]


def _camel(ten: str) -> str:
    dau, *sau = ten.split("_")
    return dau + "".join(x[:1].upper() + x[1:] for x in sau)


def _anh_veo(gia_tri) -> dict:
    """Ảnh đầu vào: data URL (mang kiểu ảnh) hoặc base64 trần (coi là PNG như mã cũ của c2a). Veo nhận
    `bytesBase64Encoded` + `mimeType` — mã c2a ghi `inlineData` bị 400 dù tài liệu Veo dùng `inlineData`."""
    if not isinstance(gia_tri, str) or not gia_tri:
        raise ValueError("ảnh đầu vào phải là chuỗi base64 hoặc data URL")
    kieu = "image/png"
    if gia_tri.startswith("data:"):
        dau, _, gia_tri = gia_tri.partition(",")
        kieu = dau.removeprefix("data:").split(";")[0] or kieu
    try:
        base64.b64decode(gia_tri, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("ảnh đầu vào không phải base64 hợp lệ") from None
    return {"bytesBase64Encoded": gia_tri, "mimeType": kieu}


def _loi(ma: int, tin: str, code: str) -> tuple:
    return tra_loi_json(ma, {"error": {"message": tin, "type": "invalid_request_error" if ma < 500 else "api_error",
                                       "code": code}})


class Module:
    #: Bản đệm danh sách model gốc và cỡ trang (thuộc tính lớp để test thu nhỏ).
    DEM_MODELS_GIAY = 600.0
    CO_TRANG_MODELS_GOC = 1000
    TIMEOUT_TAI = 120.0  # giữa hai khúc khi tải video

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._ct: ChuyenTiepOpenAI | None = None
        self._dem_goc: list[dict] | None = None
        self._dem_goc_luc = 0.0
        self._proxy_token = ""
        self._kiem_ip = _kiem_ip_tai_video
        self._khach_tai: httpx.AsyncClient | None = None

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        return phan_loai_loi(ma, loi, boi_canh)

    def _client_tai(self) -> httpx.AsyncClient:
        if self._khach_tai is None:
            self._khach_tai = httpx.AsyncClient(
                follow_redirects=False,
                transport=TransportGhimIp(self._kiem_ip),
            )
        return self._khach_tai

    def _tao(self, khoa: list[str], base: str, proxy_token: str = "") -> ChuyenTiepOpenAI:
        # Khả năng tốn tiền suc_khoe báo theo từng khoá = mọi mã đã khai ở `cung_cap` trừ chat.
        kn = tuple(x for x in self.manifest.get("cung_cap", []) if x != "chat")
        ct = ChuyenTiepOpenAI(ten="Gemini", tien_to=self.tien_to, base_url=base, khoa=khoa,
                              doi_id_model=_bo_models, phan_loai_loi=self.phan_loai_loi, kha_nang=kn,
                              goi_y_tu_choi_kha_nang=GOI_Y_HANG_MIEN_PHI)
        if proxy_token:
            goc_tieu_de = ct._tieu_de

            def _tieu_de_kem_proxy(k: str) -> dict:
                h = dict(goc_tieu_de(k))
                h["X-Proxy-Token"] = proxy_token
                return h

            ct._tieu_de = _tieu_de_kem_proxy
        return ct

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        self._proxy_token = str(cau_hinh.get("proxy_token") or "").strip()
        self._ct = self._tao(ds_khoa(cau_hinh.get("api_keys")), chuan_hoa_base_url(cau_hinh.get("base_url")), self._proxy_token)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng khoá bằng GET /models (không tốn token). Chỉ giữ khoá hợp lệ; không khoá nào → ok=false.
        KHÔNG thử tạo ảnh/video: đó là lời gọi tốn tiền — khả năng của khoá là «chua_ro» tới khi dùng thật."""
        khoa = ds_khoa(cau_hinh.get("api_keys"))
        if not khoa:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập khoá API nào (trường api_keys)."}
        try:
            base = chuan_hoa_base_url(cau_hinh.get("base_url"))
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        proxy_token = str(cau_hinh.get("proxy_token") or "").strip()
        ct = self._tao(khoa, base, proxy_token)
        try:
            ds = (await ct.suc_khoe())["khoa"]
        finally:
            await ct.dung()
        for muc in ds:
            muc.pop("dang_nghi_den", None)
            muc.pop("kha_nang", None)
        tot = [k for k, muc in zip(khoa, ds) if muc["ok"]]
        if not tot:
            loi = f"Không khoá nào gọi được {base}/models."
            if any(muc["loi"] and GOI_Y_CHAN_VUNG in muc["loi"] for muc in ds):
                loi += " " + GOI_Y_CHAN_VUNG[0].upper() + GOI_Y_CHAN_VUNG[1:] + "."
            return {"ok": False, "ket_qua_tung_khoa": ds, "loi": loi}
        cau_hinh_luu: dict[str, Any] = {"api_keys": tot, "base_url": base}
        if proxy_token:
            cau_hinh_luu["proxy_token"] = proxy_token
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": cau_hinh_luu}

    async def chat(self, body: dict, stream: bool):
        return await self._ct.chat(body, stream)

    # ── ảnh: đường tương thích OpenAI, thân đi nguyên văn (aspect_ratio, generation_config… của Google) ──

    async def anh(self, body: dict):
        _, kq = await self._ct.goi_kha_nang("anh", "tạo ảnh", "POST", f"{self._ct.base_url}/images/generations",
                                            json_than=self._ct.doi_model(body), tieu_de=self._ct._tieu_de)
        return kq

    # ── video: API gốc Veo ──

    @property
    def _goc_api(self) -> str:
        # base_url luôn kết thúc bằng /v1beta/openai (chuan_hoa_base_url) → gốc API là /v1beta, qua cùng proxy.
        return self._ct.base_url.removesuffix("/openai")

    def _tieu_de_goc(self, khoa: str) -> dict:
        h = {"x-goog-api-key": khoa}
        if self._proxy_token:
            h["X-Proxy-Token"] = self._proxy_token
        return h

    def _than_veo(self, body: dict) -> tuple[str, dict]:
        """Thân /v1/videos (OpenAI + tham số của Google) → (tên model Veo, thân predictLongRunning). ValueError = 400."""
        model = body.get("model")
        ten = model[len(self.tien_to) + 1:] if isinstance(model, str) and model.startswith(self.tien_to + "/") else ""
        if not TEN_MODEL.match(ten) or ".." in ten:
            raise ValueError(f"model {model!r} không hợp lệ (dạng {self.tien_to}/<tên model Veo>, "
                             f"ví dụ {self.tien_to}/veo-3.1-fast-generate-preview)")
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("thiếu «prompt» (mô tả video)")
        mau: dict = {"prompt": prompt}
        anh = body.get("image") or body.get("input_reference")
        if anh:
            mau["image"] = _anh_veo(anh)
        if body.get("last_frame"):
            mau["lastFrame"] = _anh_veo(body["last_frame"])
        tham_so = {_camel(k): v for k, v in body.items() if k not in _TRUONG_TU_DICH}
        if "seconds" in body:  # tên của OpenAI (chuỗi "4"/"8"…)
            tham_so.setdefault("durationSeconds", body["seconds"])
        if "durationSeconds" in tham_so:
            # Gửi SỐ: mã c2a ghi Veo trả 400 khi là chuỗi ("8"), dù bảng tham số trên trang Veo viết "4"/"6"/"8".
            # Nhận chuỗi chữ số có chủ đích; OverflowError (int(inf)) phải bắt như ValueError kẻo thành 500;
            # bool (True→1) và float không nguyên (4.5→4) bị loại — int() lặng lẽ ép kiểu là sai.
            ds = tham_so["durationSeconds"]
            if isinstance(ds, bool) or (isinstance(ds, float) and not ds.is_integer()):
                raise ValueError(f"thời lượng {ds!r} phải là số giây (4, 6 hoặc 8)")
            try:
                tham_so["durationSeconds"] = int(ds)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"thời lượng {tham_so['durationSeconds']!r} phải là số giây (4, 6 hoặc 8)") from None
        size = body.get("size")
        if isinstance(size, str) and "aspectRatio" not in tham_so:  # kích thước OpenAI «RỘNGxCAO» → tỉ lệ khung
            rong, _, cao = size.lower().partition("x")
            if not (rong.isdigit() and cao.isdigit()):
                raise ValueError(f"size {size!r} phải dạng RỘNGxCAO, ví dụ 1280x720")
            tham_so["aspectRatio"] = "16:9" if int(rong) >= int(cao) else "9:16"
        than = {"instances": [mau]}
        if tham_so:
            than["parameters"] = tham_so
        return ten, than

    def _doi_tuong_video(self, ma_video: str, ten_thao_tac: str, **them) -> tuple:
        model = ten_thao_tac.split("/")[1]
        return tra_loi_json(200, {"id": ma_video, "object": "video", "model": f"{self.tien_to}/{model}", **them})

    async def video_tao(self, body: dict):
        try:
            ten, than = self._than_veo(body)
        except ValueError as e:
            return _loi(400, str(e), "invalid_video_request")
        khoa, kq = await self._ct.goi_kha_nang("video", "tạo video", "POST",
                                               f"{self._goc_api}/models/{ten}:predictLongRunning",
                                               json_than=than, tieu_de=self._tieu_de_goc)
        if khoa is None:
            return kq
        try:
            ten_thao_tac = json.loads(kq[2]).get("name")
        except (ValueError, AttributeError):
            ten_thao_tac = None
        if not isinstance(ten_thao_tac, str) or not TEN_THAO_TAC.match(ten_thao_tac):
            return _loi(502, "Google nhận yêu cầu tạo video nhưng không trả tên thao tác hợp lệ", "upstream_invalid")
        ma = base64.urlsafe_b64encode(ten_thao_tac.encode()).decode().rstrip("=") + "." + _van_tay(khoa)
        return self._doi_tuong_video(ma, ten_thao_tac, status="queued", progress=0, created_at=int(time.time()))

    def _giai_ma(self, ma: str) -> tuple[str | None, str | None, tuple | None]:
        """Mã video của KHÁCH → (tên thao tác, khoá đã tạo, None) hoặc (None, None, lỗi 404) — kiểm chặt TRƯỚC khi
        gọi Google kèm khoá: mã tự chế không được biến thành đường tuỳ ý trên máy chủ Google."""
        khop = MA_VIDEO.match(ma)
        ten = None
        if khop:
            try:
                ten = base64.urlsafe_b64decode(khop[1] + "=" * (-len(khop[1]) % 4)).decode()
            except (binascii.Error, ValueError):
                ten = None
        if ten is None or not TEN_THAO_TAC.match(ten) or ".." in ten:
            return None, None, _loi(404, f"Không có video {ma!r}", "video_not_found")
        khoa = next((k for k in self._ct.khoa if k and _van_tay(k) == khop[2]), None)
        if khoa is None:
            return None, None, _loi(404, "Khoá đã tạo video này không còn trong cấu hình module (đã bớt khoá?) — video thuộc dự án "
                             "của khoá đó nên khoá khác không hỏi được", "video_not_found")
        return ten, khoa, None

    async def _hoi(self, ten: str, khoa: str) -> tuple[dict | None, tuple | None]:
        """GET thao tác Veo bằng đúng khoá đã tạo → (thao tác, None) hoặc (None, lỗi đã chuẩn hoá)."""
        may = urlsplit(self._goc_api).netloc
        khach = self._ct._client()
        req = khach.build_request("GET", f"{self._goc_api}/{ten}", headers=self._tieu_de_goc(khoa),
                                  timeout=httpx.Timeout(self._ct.TIMEOUT_KIEM * 3, connect=self._ct.TIMEOUT_KET_NOI))
        try:
            r = await khach.send(req, stream=True)
            noi_dung = await doc_phan_hoi_co_tran(r)
        except PhanHoiQuaLon as e:
            return None, _loi(502, f"Phản hồi từ Gemini quá lớn: {e}", "upstream_too_large")
        except TimeoutError:
            return None, _loi(504, "Gemini không trả lời kịp: quá thời gian đọc", "upstream_timeout")
        except httpx.HTTPError as e:
            return None, _loi(502, f"Không hỏi được Gemini ({may}): {type(e).__name__}", "upstream_unreachable")
        if r.status_code != 200:
            return None, tra_loi_json(r.status_code, self._ct.chuan_hoa_loi(r.status_code, noi_dung))
        try:
            j = json.loads(noi_dung)
        except ValueError:
            j = None
        if not isinstance(j, dict):
            return None, _loi(502, "Google trả trạng thái video không phải JSON", "upstream_invalid")
        return j, None

    def _trang_thai(self, j: dict) -> dict:
        """Thao tác Veo → các trường trạng thái video OpenAI (+ "_uri" nội bộ khi xong)."""
        if not j.get("done"):
            return {"status": "in_progress", "progress": None}
        if isinstance(j.get("error"), dict):
            loi = j["error"]
            return {"status": "failed", "error": {"code": str(loi.get("code") or "video_failed"),
                                                  "message": self._ct.che_khoa(str(loi.get("message") or "Google báo lỗi"))}}
        kq = (j.get("response") or {}).get("generateVideoResponse") or {}
        mau = kq.get("generatedSamples") or []
        uri = mau[0].get("video", {}).get("uri") if mau and isinstance(mau[0], dict) else None
        if isinstance(uri, str) and uri:
            return {"status": "completed", "progress": 100, "_uri": uri}
        ly_do = kq.get("raiMediaFilteredReasons")
        tin = "Google báo xong nhưng không có video"
        if isinstance(ly_do, list) and ly_do:
            tin += " — bộ lọc an toàn của Google: " + "; ".join(str(x) for x in ly_do)
        return {"status": "failed", "error": {"code": "no_video", "message": self._ct.che_khoa(tin)}}

    async def video_trang_thai(self, ma: str):
        ten, khoa, loi = self._giai_ma(ma)
        if loi is not None:
            return loi
        j, loi = await self._hoi(ten, khoa)
        if loi is not None:
            return loi
        tt = self._trang_thai(j)
        tt.pop("_uri", None)
        return self._doi_tuong_video(ma, ten, **tt)

    def _url_tai(self, uri: str) -> str:
        """URI Google trả trỏ máy Google THẬT; tải qua máy của base_url (proxy vượt chặn vùng chuyển nguyên đường
        dẫn). URI ở máy khác giữ nguyên — và sẽ được tải KHÔNG kèm khoá (_tai)."""
        if _goc(uri) == _goc(BASE_MAC_DINH):
            p = urlsplit(uri)
            return f"{urlsplit(self._goc_api).scheme}://{urlsplit(self._goc_api).netloc}{p.path}" + (
                f"?{p.query}" if p.query else "")
        return uri

    async def video_noi_dung(self, ma: str):
        ten, khoa, loi = self._giai_ma(ma)
        if loi is not None:
            return loi
        j, loi = await self._hoi(ten, khoa)
        if loi is not None:
            return loi
        tt = self._trang_thai(j)
        if tt["status"] != "completed":
            return _loi(409, f"Video chưa có nội dung (trạng thái {tt['status']}) — hỏi GET /v1/videos/{{id}} tới "
                             "«completed» rồi tải", "video_not_ready")
        return await self._tai(self._url_tai(tt["_uri"]), khoa)

    async def _tai(self, url: str, khoa: str):
        """Tải video theo STREAM, tự đi chuyển hướng: khoá chỉ gửi khi máy đích là máy của base_url (httpx tự đi
        chuyển hướng thì giữ header tự đặt như x-goog-api-key sang máy khác). Không ghi log URL (có thể mang chữ ký)."""
        khach = self._client_tai()
        goc_khoa = _goc(self._goc_api)
        timeout = httpx.Timeout(self.TIMEOUT_TAI, connect=self._ct.TIMEOUT_KET_NOI)
        for _ in range(CHUYEN_HUONG_TOI_DA + 1):
            p = urlsplit(url)
            if p.scheme not in ("http", "https") or not p.hostname:
                return _loi(400, "Địa chỉ tải video không hợp lệ", "invalid_url")
            tieu_de = self._tieu_de_goc(khoa) if _goc(url) == goc_khoa else {}
            try:
                r = await khach.send(khach.build_request("GET", url, headers=tieu_de, timeout=timeout), stream=True)
            except LoiDichBiChan as e:
                return _loi(502, f"Đích tải video bị chặn: {e}", "ssrf_blocked")
            except httpx.HTTPError as e:
                return _loi(502, f"Không tải được video: {type(e).__name__}", "upstream_unreachable")
            if r.is_redirect and r.headers.get("location"):
                await r.aclose()
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code != 200:
                try:
                    noi_dung = await doc_phan_hoi_co_tran(r)
                except PhanHoiQuaLon as e:
                    return _loi(502, f"Phản hồi tải video quá lớn: {e}", "upstream_too_large")
                except TimeoutError:
                    return _loi(504, "Tải video không trả lời kịp: quá thời gian đọc", "upstream_timeout")
                except httpx.HTTPError as e:
                    return _loi(502, f"Không đọc được phản hồi tải video: {type(e).__name__}", "upstream_unreachable")
                return tra_loi_json(r.status_code, self._ct.chuan_hoa_loi(r.status_code, noi_dung))
            return 200, {"content-type": r.headers.get("content-type", "video/mp4")}, self._luong(r)
        return _loi(502, f"Tải video: quá {CHUYEN_HUONG_TOI_DA} lần chuyển hướng", "too_many_redirects")

    def _luong(self, r: httpx.Response):
        return LuongNhiPhan(r, self._ct.log, "gemini-api")

    # ── models / sức khoẻ ──

    async def _models_goc(self) -> list[dict]:
        """Danh sách API gốc (có `supportedGenerationMethods`), đi hết các trang, đệm như danh sách chat."""
        bay_gio = self._ct._dong_ho()
        if self._dem_goc is not None and bay_gio - self._dem_goc_luc < self.DEM_MODELS_GIAY:
            return self._dem_goc
        khoa = next((k for k in self._ct.khoa if self._ct._nghi.get(k, 0.0) <= bay_gio), self._ct.khoa[0])
        ds: list[dict] = []
        trang = None
        for _ in range(50):
            tham_so = {"pageSize": self.CO_TRANG_MODELS_GOC, **({"pageToken": trang} if trang else {})}
            khach = self._ct._client()
            req = khach.build_request("GET", f"{self._goc_api}/models", params=tham_so, headers=self._tieu_de_goc(khoa),
                                      timeout=httpx.Timeout(self._ct.TIMEOUT_DOC, connect=self._ct.TIMEOUT_KET_NOI))
            r = await khach.send(req, stream=True)
            noi_dung = await doc_phan_hoi_co_tran(r)
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            j = json.loads(noi_dung)
            ds += [m for m in j.get("models") or [] if isinstance(m, dict)]
            trang = j.get("nextPageToken")
            if not trang:
                break
        self._dem_goc, self._dem_goc_luc = ds, bay_gio
        return ds

    async def models(self) -> list[dict]:
        """Danh sách chat của đường tương thích (giữ nguyên) + `kha_nang` suy từ danh sách API gốc (`_kha_nang`):
        model video/ảnh được gắn nhãn (có sẵn trong danh sách) hoặc thêm vào (chỉ có ở API gốc). Danh sách gốc
        hỏng thì trả danh sách chat không nhãn."""
        ds = [dict(m) for m in await self._ct.models()]  # chép: không sửa bản đệm của bộ chuyển tiếp
        try:
            goc = await self._models_goc()
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            self._ct.log.warning("Gemini: không lấy được danh sách model gốc (%s) — chỉ trả danh sách chat",
                                 self._ct.che_khoa(f"{type(e).__name__}: {e}"))
            return ds
        theo_id = {m["id"]: m for m in ds}
        for m in goc:
            ten = m.get("name")
            pt = m.get("supportedGenerationMethods")
            if not isinstance(ten, str) or not isinstance(pt, list):
                continue
            kn = _kha_nang(_bo_models(ten), pt)
            if not kn:
                continue
            id_ = f"{self.tien_to}/{_bo_models(ten)}"
            if id_ in theo_id:
                theo_id[id_]["kha_nang"] = kn
            else:
                muc = {"id": id_, "object": "model", "owned_by": "google", "kha_nang": kn}
                ds.append(muc)
                theo_id[id_] = muc
        return ds

    async def suc_khoe(self) -> dict:
        return await self._ct.suc_khoe()

    async def dung(self) -> None:
        if self._khach_tai is not None:
            await self._khach_tai.aclose()
            self._khach_tai = None
        if self._ct is not None:
            await self._ct.dung()
