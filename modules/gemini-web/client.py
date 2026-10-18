"""Máy khách cho giao diện web gemini.google.com (giao thức batchexecute nội bộ).

Cách giao tiếp học từ kho tham khảo chatgpt2api (api/gemini_web.py) — kho đó dùng
gói thứ ba ``gemini_webapi`` (HanaokaYuzu/Gemini-API). Gói đó không có trong ảnh
web2api và không cài được ở đây, nên module tự cài đặt tối thiểu bằng ``httpx``
theo ĐÚNG luồng đã học được:

1. Xác thực bằng hai cookie của tab gemini.google.com đã đăng nhập:
   ``__Secure-1PSID`` (bắt buộc) và ``__Secure-1PSIDTS`` (tùy chọn; kho tham khảo
   xoay nó ~600 giây một lần để giữ phiên sống).
2. ``init()``: GET trang chủ ``https://gemini.google.com/`` kèm cookie; cookie
   hỏng → 401/403. Bóc mã phiên ``SNlM0e`` từ HTML (mẫu ``"SNlM0e":"..."``) —
   mã này đi kèm mọi lời gọi batchexecute dưới dạng tham số ``at``.
3. Trò chuyện: POST ``/_/BardChatUi/data/batchexecute`` với dữ liệu form
   ``f.req`` (mảng JSON lồng nhau, chứa prompt và tên model) và ``at``.
   Đáp án bắt đầu bằng ``)]}'`` rồi là các khung JSON ``["wrb.fr", rpcid,
   "<json lồng>", ...]``; văn bản nằm trong danh sách ứng viên ở vị trí đã
   ghi trong tài liệu của hàm trích xuất.
4. Stream: cùng endpoint, đọc đáp án dần dần; mỗi khung mới mang toàn bộ văn
   bản tới thời điểm đó nên client tự tính ``delta`` so với khung trước
   (kho tham khảo gọi phần chênh này là ``text_delta``).

Giao thức web của Google KHÔNG chính thức và có thể đổi bất cứ lúc nào —
module này CHƯA KIỂM với dịch vụ thật, chỉ kiểm với máy chủ giả trong test/.
Mọi lỗi đều được bọc thành ngoại lệ có cấu trúc (mã HTTP + thông điệp chung),
KHÔNG bao giờ mang nội dung đáp án thô của Google hay cookie ra ngoài.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.lam_moi_model import TOI_DA_MODEL, chuan_hoa_ma_model

log = logging.getLogger("web2api.module.gemini-web")

#: Máy chủ web Gemini (hằng số — không phải do người dùng nhập).
MAY_CHU_MAC_DINH = "https://gemini.google.com"

#: RPC cho tạo nội dung (theo luồng của gói tham khảo).
_RPC_TAO_NOI_DUNG = "hNvQH"

_MAU_SNLM0E = re.compile(r'"SNlM0e"\s*:\s*"([^"]+)"')

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# Ngoại lệ có cấu trúc: chỉ mang mã + thông điệp chung, không mang cookie
# hay nội dung đáp án thô của nhà cung cấp.
# ---------------------------------------------------------------------------

class LoiGeminiWeb(Exception):
    """Lỗi chung của máy khách; ``ma`` là HTTP status tương ứng để trả khách."""

    def __init__(self, ma: int, thong_diep: str):
        super().__init__(thong_diep)
        self.ma = ma


class LoiXacThuc(LoiGeminiWeb):
    """Cookie bị từ chối (401/403) — tài khoản này hỏng, nên chuyển tài khoản."""

    def __init__(self, thong_diep: str = "cookie Google không được gemini.google.com chấp nhận"):
        super().__init__(401, thong_diep)


class LoiHanMuc(LoiGeminiWeb):
    """Bị giới hạn lượt (429)."""

    def __init__(self, thong_diep: str = "gemini.google.com giới hạn lượt gọi (429)"):
        super().__init__(429, thong_diep)


class LoiMayChu(LoiGeminiWeb):
    """Lỗi phía Google (5xx) hoặc đáp án sai cấu trúc — không phải lỗi tài khoản."""

    def __init__(self, thong_diep: str = "gemini.google.com lỗi phía máy chủ"):
        super().__init__(502, thong_diep)


class LoiHetGio(LoiGeminiWeb):
    """Hết giờ chờ nối/đọc."""

    def __init__(self, thong_diep: str = "gemini.google.com không phản hồi kịp"):
        super().__init__(504, thong_diep)


class LoiNoiDungQuaDai(LoiGeminiWeb):
    """Nội dung yêu cầu quá dài (413) — vượt quá trần ký tự của client web."""

    def __init__(self, thong_diep: str = "nội dung yêu cầu quá dài (vượt quá giới hạn xử lý của client gemini-web)"):
        super().__init__(413, thong_diep)


class ModelKhongCo(ValueError):
    """Tên model người dùng chỉ đích danh nhưng module không biết → 400."""


# ---------------------------------------------------------------------------
# Registry model: bí danh thân thiện như kho tham khảo (3.5-flash → gemini-3-flash…).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registry model (NV93/NV91-P3): mã ỔN ĐỊNH, nhãn hiển thị tách riêng.
# Tên webui đổi chỉ đổi nhãn — mã trong /v1/models và khoá tài khoản không đổi.
# CHƯA KIỂM với dịch vụ thật: bảng suy từ kho tham khảo, có thể thiếu model.
# ---------------------------------------------------------------------------

#: Mã ổn định → nhãn hiển thị.
MO_HINH_ON_DINH = {
    "gemini-3-flash": "Gemini 3 Flash",
    "gemini-3-pro": "Gemini 3 Pro",
}

#: Bí danh người dùng gõ → mã ổn định (tương thích ngược tên cũ).
BI_DANH_MODEL = {
    "3.5-flash": "gemini-3-flash",
    "3.1-pro": "gemini-3-pro",
    "3.5-flash-lite": "gemini-3-flash",
    "flash": "gemini-3-flash",
    "pro": "gemini-3-pro",
    "flash-lite": "gemini-3-flash",
    "auto": "gemini-3-flash",
    "gemini-3-flash": "gemini-3-flash",
    "gemini-3-pro": "gemini-3-pro",
    "gemini-flash-lite": "gemini-3-flash",
}

#: Tên thân thiện hiện trong /v1/models (mã ổn định, không tiền tố).
MO_HINH_HO_TRO = ("gemini-3-flash", "gemini-3-pro")

MODEL_MAC_DINH = "gemini-3-flash"

#: Mã ổn định → tên web Gemini hiểu trong f.req (CHƯA KIỂM thật).
TEN_CHUAN_WEB = {
    "gemini-3-flash": "gemini-3-flash",
    "gemini-3-pro": "gemini-3-pro",
}

_TIEN_TO_STRIP = ("gemini-web/", "gma/", "gemini_web_api/")


def nhan_model(ma_on_dinh: str) -> str:
    """Nhãn hiển thị của một mã model ổn định (tách khỏi id)."""
    return MO_HINH_ON_DINH.get(str(ma_on_dinh or ""), str(ma_on_dinh or ""))


def giai_quyet_model(ten: str | None, biet_them: tuple[str, ...] | list[str] | set[str] = ()) -> str:
    """Tên model (đã/không kèm tiền tố) → mã ổn định; lạ → ModelKhongCo.

    Quy tắc: chỉ ``auto`` (hoặc rỗng) được rơi về mặc định; người dùng CHỈ
    ĐÍCH DANH tên lạ phải nhận 400, không âm thầm chạy model khác.
    """
    raw = str(ten or "").strip()
    m = raw
    for pfx in _TIEN_TO_STRIP:
        if m.lower().startswith(pfx):
            m = m[len(pfx):]
            break
    if not m or m.lower() == "auto":
        return MODEL_MAC_DINH
    if m in biet_them:
        return m
    m_lower = m.lower()
    if m_lower in biet_them:
        return m_lower
    if m_lower in BI_DANH_MODEL:
        return BI_DANH_MODEL[m_lower]
    raise ModelKhongCo(f"model {ten!r} không có trong danh sách hỗ trợ")


def ten_chuan_cua_model(ma_on_dinh: str) -> str:
    """Mã ổn định → tên gửi trong f.req."""
    return TEN_CHUAN_WEB.get(str(ma_on_dinh or ""), str(ma_on_dinh or ""))


# ---------------------------------------------------------------------------
# Khám phá model theo tài khoản (NV91-P3): đọc từ trang chủ đã đăng nhập.
# ---------------------------------------------------------------------------

_MAU_MODEL_LIST = re.compile(r'<script id="model-list"[^>]*>(.*?)</script>',
                             re.S | re.I)


def trich_model_tu_html(html: str | None) -> list[str]:
    """Bóc danh sách mã model từ HTML trang chủ (khối model-list nhúng).

    Mã lấy ra phải qua chuan_hoa_ma_model, bỏ trùng, cắt ở TOI_DA_MODEL.
    Chuẩn hoá về mã ổn định khi nhận ra, giữ nguyên tên lạ. Không có khối → [].
    """
    khop = _MAU_MODEL_LIST.search(str(html or ""))
    if not khop:
        return []
    try:
        du_lieu = json.loads(khop.group(1))
    except (ValueError, TypeError):
        return []
    ds = du_lieu.get("models") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list):
        return []
    ket_qua = []
    da_thay = set()
    for m in ds:
        if not isinstance(m, str):
            continue
        chuan = chuan_hoa_ma_model(m)
        if chuan is None:
            continue
        on_dinh = BI_DANH_MODEL.get(chuan.lower(), chuan)
        if on_dinh not in da_thay:
            da_thay.add(on_dinh)
            ket_qua.append(on_dinh)
            if len(ket_qua) >= TOI_DA_MODEL:
                break
    return ket_qua


# ---------------------------------------------------------------------------
# Bóc đáp án batchexecute.
# ---------------------------------------------------------------------------

def _tach_khung(bo_dem: str):
    """Tách các khung JSON hoàn chỉnh khỏi bộ đệm; trả (khung, phần_dư).

    Đáp án batchexecute là các mảng JSON nối tiếp nhau (có tiền tố ``)]}'`` ở
    đầu). Dùng raw_decode nên chịu được cả đáp án dồn một lần lẫn stream
    từng mảnh.
    """
    khung = []
    bo_dem = bo_dem.lstrip()
    if bo_dem.startswith(")]}'"):
        bo_dem = bo_dem[4:].lstrip("\r\n")
    giai = json.JSONDecoder()
    while bo_dem.strip():
        try:
            doi_tuong, vi_tri = giai.raw_decode(bo_dem)
        except json.JSONDecodeError:
            break
        khung.append(doi_tuong)
        bo_dem = bo_dem[vi_tri:].lstrip()
    return khung, bo_dem


def _van_ban_ung_vien(ung_vien) -> str:
    """Trích văn bản từ một ứng viên: [cid, noi_dung, ...]; noi_dung là chuỗi
    hoặc danh sách các phần ([van_ban, ...] hoặc chuỗi)."""
    if not isinstance(ung_vien, list) or len(ung_vien) < 2:
        return ""
    noi_dung = ung_vien[1]
    if isinstance(noi_dung, str):
        return noi_dung
    if isinstance(noi_dung, list):
        phan = []
        for p in noi_dung:
            if isinstance(p, str):
                phan.append(p)
            elif isinstance(p, list) and p and isinstance(p[0], str):
                phan.append(p[0])
        return "".join(phan)
    return ""


def trich_van_ban_khung(khung) -> str:
    """Trích toàn bộ văn bản từ một khung ``["wrb.fr", rpcid, "<json>", ...]``.

    JSON lồng: danh sách mà phần tử [4] là danh sách ứng viên (theo cấu trúc
    đáp án của luồng tham khảo). Không đúng cấu trúc → chuỗi rỗng (không vỡ).
    """
    try:
        if not isinstance(khung, list) or len(khung) < 3 or khung[0] != "wrb.fr":
            return ""
        du_lieu = json.loads(khung[2])
        if not isinstance(du_lieu, list) or len(du_lieu) < 5:
            return ""
        ung_viens = du_lieu[4]
        if not isinstance(ung_viens, list):
            return ""
        return "".join(_van_ban_ung_vien(c) for c in ung_viens)
    except (ValueError, TypeError, IndexError):
        return ""


#: Giới hạn tối đa ký tự prompt (tránh quá tải/tràn bộ nhớ client web).
TOI_DA_KY_TU_PROMPT = 120_000


# ---------------------------------------------------------------------------
# Máy khách.
# ---------------------------------------------------------------------------

class GeminiWebClient:
    """Phiên HTTP đã xác thực bằng cookie cho một tài khoản Google."""

    def __init__(self, psid: str, psidts: str | None = None,
                 base_url: str = MAY_CHU_MAC_DINH,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 120.0):
        self._psid = psid
        self._snlm0e: str | None = None
        self._dong = False
        jar = {"__Secure-1PSID": psid}
        if psidts:
            jar["__Secure-1PSIDTS"] = psidts
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            cookies=jar,
            transport=transport,
            timeout=httpx.Timeout(connect=15.0, read=timeout, write=15.0, pool=15.0),
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                "x-same-domain": "1",
                "Origin": MAY_CHU_MAC_DINH,
                "Referer": MAY_CHU_MAC_DINH + "/",
            },
        )

    def __repr__(self):  # không bao giờ in cookie ra
        return f"GeminiWebClient(da_init={self._snlm0e is not None})"

    def _kiem_ma(self, ma: int, buoc: str) -> None:
        if ma in (401, 403):
            raise LoiXacThuc(f"cookie không được chấp nhận ở bước {buoc} (HTTP {ma})")
        if ma == 429:
            raise LoiHanMuc(f"giới hạn lượt gọi ở bước {buoc}")
        if ma >= 500:
            raise LoiMayChu(f"máy chủ lỗi ở bước {buoc} (HTTP {ma})")
        if ma >= 400:
            raise LoiMayChu(f"đáp án lạ ở bước {buoc} (HTTP {ma})")

    async def init(self) -> None:
        """Lấy mã phiên SNlM0e; cookie hỏng → LoiXacThuc."""
        if self._dong:
            raise LoiMayChu("client đã đóng")
        try:
            r = await self._client.get("/")
        except httpx.TimeoutException as e:
            raise LoiHetGio(f"hết giờ chờ tải trang chủ: {type(e).__name__}") from None
        except httpx.HTTPError as e:
            raise LoiMayChu(f"không nối được gemini.google.com: {type(e).__name__}") from None
        self._kiem_ma(r.status_code, "init")
        khop = _MAU_SNLM0E.search(r.text)
        if not khop:
            # Trang đăng nhập Google cũng trả 200 nhưng không có SNlM0e.
            if "accounts.google.com" in r.text or "signin" in r.text.lower():
                raise LoiXacThuc("cookie hết hạn — trang chủ chuyển về đăng nhập")
            raise LoiMayChu("không lấy được mã phiên SNlM0e từ trang chủ")
        self._snlm0e = khop.group(1)
        # httpx tự cập nhật cookie jar từ Set-Cookie (cơ chế xoay __Secure-1PSIDTS
        # trong phiên, như auto_refresh của gói tham khảo nhưng chỉ trong RAM).

    async def lay_model_kha_dung(self) -> list[str]:
        """Mã model tài khoản này dùng được (NV91-P3): đọc khối model-list
        nhúng trong trang chủ đã đăng nhập. 401/403 → LoiXacThuc."""
        if self._dong:
            raise LoiMayChu("client đã đóng")
        try:
            r = await self._client.get("/")
        except httpx.TimeoutException as e:
            raise LoiHetGio(f"hết giờ chờ tải trang chủ: {type(e).__name__}") from None
        except httpx.HTTPError as e:
            raise LoiMayChu(f"không nối được gemini.google.com: {type(e).__name__}") from None
        self._kiem_ma(r.status_code, "lay_model")
        return trich_model_tu_html(r.text)

    async def _tham_so_va_du_lieu(self, prompt: str, ten_model: str):
        if self._snlm0e is None:
            await self.init()
        req_arr = [[prompt], None, [ten_chuan_cua_model(ten_model)]]
        f_req = json.dumps([None, json.dumps(req_arr)])
        tham_so = {"rpcids": _RPC_TAO_NOI_DUNG, "source-path": "/", "hl": "vi", "rt": "c"}
        du_lieu = {"f.req": f_req, "at": self._snlm0e}
        return tham_so, du_lieu

    async def generate_content_stream(self, prompt: str, model: str | None = None,
                                      biet_them: tuple[str, ...] | list[str] | set[str] = (),
                                      ten_model_da_giai: str | None = None):
        """Async generator ra từng delta văn bản (kiểu text_delta của gói tham khảo)."""
        ten_model = ten_model_da_giai if ten_model_da_giai is not None else giai_quyet_model(model, biet_them=biet_them)
        tham_so, du_lieu = await self._tham_so_va_du_lieu(prompt, ten_model)
        try:
            async with self._client.stream(
                "POST", "/_/BardChatUi/data/batchexecute",
                params=tham_so, data=du_lieu,
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            ) as r:
                self._kiem_ma(r.status_code, "chat")
                bo_dem = ""
                truoc = ""
                dem_khung = 0
                async for manh in r.aiter_text():
                    bo_dem += manh
                    khung_moi, bo_dem = _tach_khung(bo_dem)
                    for k in khung_moi:
                        if not (isinstance(k, list) and len(k) >= 3 and k[0] == "wrb.fr"):
                            continue
                        dem_khung += 1
                        hien_tai = trich_van_ban_khung(k)
                        if not hien_tai:
                            continue
                        # Khung mới thường mang toàn bộ văn bản tới lúc đó → chỉ
                        # nhả phần chênh (delta); khung lặp lại y hệt thì bỏ qua.
                        if hien_tai.startswith(truoc):
                            delta = hien_tai[len(truoc):]
                        else:
                            delta = hien_tai
                        truoc = hien_tai
                        if delta:
                            yield delta
                if dem_khung == 0:
                    # 200 nhưng không có khung batchexecute nào: Google đổi định
                    # dạng — báo 502 có cấu trúc thay vì trả văn bản rỗng gây hiểu lầm.
                    raise LoiMayChu("đáp án không đúng cấu trúc batchexecute")
        except LoiGeminiWeb:
            raise
        except httpx.TimeoutException as e:
            raise LoiHetGio(f"hết giờ chờ đáp án: {type(e).__name__}") from None
        except httpx.HTTPError as e:
            raise LoiMayChu(f"mất kết nối khi đang chat: {type(e).__name__}") from None

    async def generate_content(self, prompt: str, model: str | None = None,
                               biet_them: tuple[str, ...] | list[str] | set[str] = (),
                               ten_model_da_giai: str | None = None) -> str:
        """Bản không stream: gom các delta thành văn bản đầy đủ."""
        phan = [d async for d in self.generate_content_stream(prompt, model, biet_them=biet_them, ten_model_da_giai=ten_model_da_giai)]
        return "".join(phan)

    async def close(self) -> None:
        self._dong = True
        await self._client.aclose()
