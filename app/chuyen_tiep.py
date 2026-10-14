"""Bộ chuyển tiếp THẲNG tới một dịch vụ nói chuẩn OpenAI — dùng chung cho mọi module AI.

Đây là "bộ công cụ" lõi cấp cho module (mọi module AI đều dùng): module
chỉ khai điểm riêng (base_url, cách đổi tên model, hook phân loại lỗi), còn phần dễ sai nằm MỘT chỗ:

- Thân yêu cầu đi NGUYÊN VĂN, chỉ đổi `model` bỏ tiền tố. Không lọc trường lạ: một số model
  gắn chữ ký suy nghĩ vào `tool_calls[].extra_content...thought_signature`; mất nó là
  400 "Function call is missing a thought_signature" ở bước gọi công cụ thứ hai.
- Stream chuyển tiếp TỪNG DÒNG SSE ngay khi tới, không gom bộ đệm (người dùng thấy chữ
  hiện dần; HA thoại bắt đầu đọc sớm).
- Xoay vòng khoá, bỏ qua khoá đang nghỉ; chỉ đổi khoá TRƯỚC byte đầu tiên gửi khách.
  Mỗi lượt thử chốt khoá vào biến cục bộ: property tự xoay đọc
  4 lần trong một nhánh làm khoá bị đánh dấu không phải khoá vừa lỗi (một yêu cầu từng nã
  nhà cung cấp 981 lượt trong ~150 s).
- Lỗi chuẩn hoá về {"error": {...}} (nhà cung cấp đôi khi trả MẢNG [{"error": …}]); khoá xuất hiện
  trong thông điệp lỗi bị thay bằng dạng che trước khi ra khỏi tiến trình.

Phân loại lỗi (quyết định đổi khoá hay trả ngay):
  429            → khoá nghỉ theo Retry-After (mặc định 60 s, trần 10 phút), thử khoá kế.
  401/403        → khoá hỏng (nghỉ 1 giờ), thử khoá kế — TRỪ khi module nhận ra lỗi không
                   thuộc khoá (vd chặn vùng: mọi khoá đều sẽ hỏng y hệt) → trả ngay, không đánh dấu.
  5xx/lỗi mạng   → thử thêm ĐÚNG MỘT khoá nữa (sự cố phía máy chủ, nã hết khoá chỉ tốn hạn mức).
  400            → nhà cung cấp có thể trả 400 cả cho KHOÁ SAI/bị thu hồi (đo khi đường tương thích trả MẢNG
                   [{"error": {"code": 400, "status": "INVALID_ARGUMENT", …}}], không có `reason`), nên mã 400 một
                   mình không nói được lỗi của khoá hay của yêu cầu. Phân xử bằng SO SÁNH GIỮA CÁC KHOÁ, không dò chữ:
                   - khoá ĐÃ từng phục vụ chat thành công → lỗi của yêu cầu, trả ngay, không đổi khoá, không đánh
                     dấu; nhưng khoá về «chưa rõ» (khoá bị thu hồi sau khi đã chạy tốt thì lần 400 sau được phân xử);
                   - khoá chưa rõ → thử khoá kế (mỗi khoá một lần, không tốn lượt thử thêm của 5xx); khoá sau trả 200
                     thì mọi khoá chưa rõ đã 400 trước nó bị đánh dấu hỏng (nghỉ 1 giờ như 401/403);
                   - MỌI khoá đã thử đều 400 → lỗi của yêu cầu: trả 400 của nhà cung cấp nguyên văn (đã che khoá),
                     KHÔNG đánh dấu khoá nào — một yêu cầu sai không được khoá cả pool.
                   Chặn vùng (`chan_vung`) trả ngay như 401/403. Trần số lần gọi vẫn là số khoá đang dùng được.
                   Giá phải trả: khách gửi đi gửi lại cùng một thân sai thì dần mọi khoá về «chưa rõ», mỗi lượt sai
                   tốn tới N lần gọi thay vì 1 (vẫn trong trần). Nếu nhà cung cấp trả 400 (không phải 404) cho
                   model mà chỉ hạng của khoá khác dùng được, khoá kia bị nghỉ chat 1 giờ.
                   Ảnh/video theo cùng quy tắc so giữa các khoá (mục «400» dưới), trạng thái nhớ riêng theo (khoá,
                   khả năng) — KHÁC một chỗ: khoá đã dùng được gặp 400 KHÔNG về «chưa rõ», vì ở đó `dung_duoc` còn
                   quyết thời gian nghỉ khi 429 (xoá nó là đổi luôn cách nghỉ 429). Vì khác biệt ấy và vì kho trạng
                   thái khác nhau (`_nghi` theo khoá với `_kn` theo (khoá, khả năng)) nên hai vòng xoay giữ riêng.
  404            → «model không có» có thể là thuộc tính của (KHOÁ, MODEL), không của yêu cầu (đo 12/10/2026: khoá thuộc
                   dự án mới bị nhà cung cấp trả 404 «no longer available to new users» cho một model cũ trong khi khoá
                   khác cùng pool trả 200 — xoay vòng làm ~1/3 lượt chat model đó lỗi). Cùng nguyên tắc với 400 nhưng
                   nhớ theo (khoá, model) và KHÔNG đánh dấu khoá hỏng (khoá vẫn phục vụ model khác):
                   - (khoá, model) ĐÃ từng phục vụ thành công → lỗi của yêu cầu, trả ngay; cặp về «chưa rõ»;
                   - chưa rõ → thử khoá kế (mỗi khoá một lần, không tốn lượt thử thêm của 5xx); khoá sau trả 200 thì
                     mọi khoá đã 404 trước nó được ghi vào sổ «model không có ở khoá này» NGHI_MODEL_KHONG_CO (6 giờ;
                     hết hạn thì thử lại); lượt sau cặp trong sổ bị bỏ qua như khoá đang nghỉ, CHỈ cho model đó —
                     trừ khi mọi khoá dùng được đều trong sổ cho model đó: khi ấy vẫn thử chúng, kết quả quyết như trên;
                   - MỌI khoá đã thử đều 404 → trả 404 của nhà cung cấp nguyên văn (đã che khoá), KHÔNG ghi sổ cặp nào
                     (model thật sự không tồn tại thì không được khoá cả pool).
                   Giá phải trả: model gõ sai tốn tới N lần gọi thay vì 1 (vẫn trong trần số khoá).
  khác (4xx)     → lỗi của yêu cầu → trả ngay, không đổi khoá.
Trạng thái «đã phục vụ thành công» và sổ «model không có» nằm trong bộ nhớ của thể hiện — lưu lại cấu hình hay khởi
động lại thì về «chưa rõ».

KHẢ NĂNG TỐN TIỀN (ảnh, video — `goi_kha_nang`; giọng nói `noi`, nhận dạng `nghe`, sửa ảnh `anh_sua` của module từ
xa cũng đi qua đây): cùng một khoá có thể chat được mà không được tạo ảnh/video
(hạng miễn phí của một số nhà cung cấp: «Not available»). Nên trạng thái nhớ theo (khoá, khả năng), TÁCH khỏi `_nghi` của
chat — khoá bị từ chối tạo ảnh vẫn chat bình thường. Phân loại theo MÃ TRẠNG THÁI và so sánh giữa các khoá,
không theo chữ trong thông điệp (dạng lỗi thật của khoá miễn phí CHƯA ĐO):
  401/403        → khoá bị từ chối khả năng đó, nghỉ 24 giờ (hết giờ thì thử lại: khoá nâng lên trả phí tự dùng
                   được), thử khoá kế — TRỪ chặn vùng (`chan_vung`): lỗi của máy chủ → trả ngay, không đánh dấu.
  429            → khoá ĐÃ từng dùng được khả năng này → hết hạn mức thật, nghỉ theo Retry-After; CHƯA từng →
                   nghỉ 24 giờ như bị từ chối (nhà cung cấp hay báo «hạn mức = 0» của hạng miễn phí bằng 429 — chưa đo;
                   nghỉ theo Retry-After thì khoá miễn phí bị nã lại mỗi phút và khách tưởng chỉ quá tải). Giá phải
                   trả: khoá trả phí MỚI gặp 429 ngay lần đầu cũng nghỉ việc đó 24 giờ (khoá khác vẫn phục vụ; lưu
                   lại cấu hình là xoá). Thử khoá kế.
  400            → khoá ĐÃ từng dùng được khả năng này → lỗi của yêu cầu, trả ngay. Chưa rõ → thử khoá kế; khoá
                   sau thành công thì khoá trước bị từ chối (24 giờ); MỌI khoá đều 400 → coi là lỗi yêu cầu, trả
                   400 của nhà cung cấp kèm gợi ý, KHÔNG đánh dấu (một yêu cầu sai không được khoá cả nhà
                   khỏi ảnh/video suốt một ngày).
  5xx/lỗi mạng   → như chat.
  404/khác       → lỗi của yêu cầu → trả ngay (CHƯA so giữa các khoá như chat — chưa đo nhà cung cấp trả 404
                   theo khoá ở đây).
Với `noi`/`nghe`/`anh_sua` nghĩa là: khoá bị từ chối giọng nói (401/403) chỉ nghỉ VIỆC đó, chat vẫn chạy và ngược lại;
429 ngay lần dùng ĐẦU cũng nghỉ 24 giờ và lỗi cuối là 403 `no_key_permitted` (khoá đã từng dùng được thì 429 kèm
Retry-After); 501 tính là lỗi máy chủ (thử thêm một khoá); 400 phân xử bằng so sánh giữa các khoá như trên.
Không bao giờ dò khoá bằng lời gọi tốn tiền: trạng thái là «chua_ro» cho tới khi dùng thật. Trạng thái nằm
trong bộ nhớ của thể hiện — lưu lại cấu hình hay khởi động lại thì về «chua_ro».

BỘ ĐẾM DÙNG TỪNG KHOÁ (`_ghi_dung` — chỗ ghi DUY NHẤT, gọi ở 4 điểm: chat 200, chat 429, khả năng 2xx, khả năng 429):
số lượt phục vụ thành công theo việc ("chat" / mã khả năng), số lần 429, lần dùng cuối, lần 429 cuối, và khoá phục vụ
yêu cầu GẦN NHẤT («đang dùng»). Không ghi thân yêu cầu; kiểm khoá (GET /models), danh sách model, hỏi trạng thái hay tải
video KHÔNG tính — bấm «Làm mới» sức khoẻ không được đổi «đang dùng». Cùng vòng đời với `_chat_duoc`/`_kn`: trong bộ nhớ của
thể hiện, nên về 0 khi khởi động lại hoặc lưu lại cấu hình (module tạo bộ chuyển tiếp mới ở `nap`). `suc_khoe` báo theo
từng khoá: `thu_tu` (vị trí trong danh sách đã lưu, từ 1 — KHÔNG phải «ưu tiên»: vẫn xoay vòng), `dang_dung`,
`dung_cuoi`, `so_429`, `lan_429_cuoi` và `han_muc` (hợp đồng ở app/kham_pha.py) với `toi_da` LUÔN None: API kiểu OpenAI
không cho hỏi hạn mức còn lại, còn «trần cấu hình − số lượt đếm từ lúc khởi động» là con số bịa (trần nhà cung cấp hồi
theo lịch của họ, bộ đếm này về 0 theo lịch của mình). Module nào đọc được trần + đã dùng THẬT từ nhà cung cấp thì tự
điền `han_muc` trong `suc_khoe` của nó.
"""

from __future__ import annotations

import asyncio
import email.utils
import ipaddress
import json
import logging
import re
import socket
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Callable
from urllib.parse import urlsplit

import httpx

from app.kham_pha import LOAI_LOI_CHUAN, phan_loai_loi_mac_dinh
from app.kho import che
from app.loi_openai import loai_theo_ma
from app.yeu_cau_da_phan import YeuCauDaPhan

MAU_CONTENT_TYPE = re.compile(r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$")


def tieu_de_cho_khach(r: httpx.Response, kieu_mac_dinh: str) -> dict[str, str]:
    """Header của dịch vụ xa được đi tới khách — MẶC ĐỊNH TỪ CHỐI, chỉ `content-type` (vắng thì `kieu_mac_dinh`).

    Chỗ DUY NHẤT header của phản hồi ngược dòng thành header trả khách (chat, khả năng tốn tiền, giọng nói, multipart).
    Dịch vụ xa không được đặt cookie lên origin của lõi (Set-Cookie), mở CORS, bật hộp đăng nhập của trình duyệt
    (WWW-Authenticate), ép tải về/đổi tên tệp (Content-Disposition), đè CSP của lõi, lộ phần mềm nội bộ (Server,
    X-Powered-By) hay chen khung kết nối (Connection, Keep-Alive, Transfer-Encoding). Content-Length/Content-Encoding
    cũng không: httpx đã giải nén, chuyển nguyên là khách giải nén lần hai. Retry-After của 429 do lõi tự tính từ sổ
    nghỉ (`_con_nghi`, `_loi_het_khoa`), không lấy của dịch vụ xa. Cùng nguyên tắc với danh sách cho phép ở app/novnc.py."""
    return {"content-type": r.headers.get("content-type", kieu_mac_dinh)}


class LuongNhiPhan:
    """Luồng nhị phân của MỘT phản hồi ngược dòng, chuyển từng khúc `aiter_bytes()` (không cắt dòng — byte nào cũng giữ).

    Lớp thay vì async generator: `aclose()` của generator CHƯA chạy (khách ngắt trước byte đầu) không chạy `finally`,
    kết nối ngược dòng treo trong pool. Ở đây `aclose()` luôn đóng phản hồi. Lỗi giữa chừng (đứt kết nối, gzip hỏng)
    KHÔNG bị nuốt: đi lên để lõi cắt kết nối tới khách (khách thấy luồng chunked không kết thúc, nhật ký ghi `dut`) —
    nhị phân không có chỗ chen sự kiện lỗi như SSE, kết thúc êm là khách tưởng tệp âm thanh đã trọn."""

    def __init__(self, r: httpx.Response, log: logging.Logger, ten: str):
        self._r = r
        self._it = r.aiter_bytes()
        self._log = log
        self._ten = ten

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._it.__anext__()
        except StopAsyncIteration:
            await self.aclose()
            raise
        except httpx.HTTPError as e:
            self._log.warning("%s: luồng nhị phân đứt giữa chừng: %s", self._ten, type(e).__name__)
            await self.aclose()
            raise

    async def aclose(self) -> None:
        try:
            await self._it.aclose()
        finally:
            await self._r.aclose()


def lam_sach_ten_tep(ten: str, mac_dinh: str = "tep") -> str:
    """Làm sạch tên tệp do khách khai: không làm đường dẫn, bỏ ký tự điều khiển, bỏ ../."""
    import urllib.parse
    s = str(ten or "")
    try:
        s = urllib.parse.unquote(s)
    except Exception:
        pass
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    s = s.replace("\\", "/").split("/")[-1].strip()
    while s.startswith("."):
        s = s.lstrip(".")
    s = s.strip()
    return s or mac_dinh


def lam_sach_content_type(ct: str) -> str:
    """Chỉ nhận dạng type/subtype hợp lệ, không thì application/octet-stream."""
    s = str(ct or "").strip()
    if MAU_CONTENT_TYPE.match(s):
        return s
    return "application/octet-stream"


NGHI_429_MAC_DINH = 60.0
NGHI_429_TRAN = 600.0
NGHI_KHOA_HONG = 3600.0
NGHI_KHA_NANG = 24 * 3600.0
NGHI_MODEL_KHONG_CO = 6 * 3600.0  # (khoá, model) bị 404 trong khi khoá khác phục vụ được model đó

class LoiDichBiChan(httpx.ConnectError):
    """Tên máy của base_url lúc KẾT NỐI phân giải ra IP bị chặn (DNS rebinding: kiểm lúc đăng ký thấy IP sạch, lúc gọi
    DNS đổi sang IP bị chặn). Là lỗi vận chuyển (ConnectError) để mọi nơi bắt `httpx.HTTPError` tự coi là không nối được."""


class TransportGhimIp(httpx.AsyncBaseTransport):
    """Ghim IP lúc KẾT NỐI — chống DNS rebinding («kiểm lúc đăng ký, dùng lúc gọi»).

    Đặt ở TẦNG VẬN CHUYỂN nên MỌI lời gọi đi qua client (chat, ảnh, giọng nói, models, kiểm khoá, và cả chỗ thêm sau này)
    đều bị kiểm, không phải nhớ gọi ở từng điểm. Mỗi yêu cầu: phân giải tên máy NGAY trước khi gửi, kiểm MỌI IP bằng
    `kiem_ip` (hàm chung `app.tu_xa._dia_chi_bi_chan`), rồi nối thẳng tới IP đã kiểm đầu tiên mà qua. Header Host giữ
    nguyên tên gốc (httpx đặt từ URL gốc) và SNI/kiểm chứng chỉ TLS dùng tên gốc (extension `sni_hostname`), khuôn của
    `app/novnc.py`. IP chữ trong URL cũng bị kiểm (không cần phân giải). Không IP nào qua → LoiDichBiChan."""

    def __init__(self, kiem_ip: Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], str | None],
                 goc: httpx.AsyncBaseTransport | None = None):
        self._kiem_ip = kiem_ip
        self._goc = goc or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        may = request.url.host
        cong = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            ds = [ipaddress.ip_address(may)]
            la_ip_chu = True
        except ValueError:
            la_ip_chu = False
            try:
                tt = await asyncio.get_running_loop().getaddrinfo(may, cong, type=socket.SOCK_STREAM)
            except OSError:
                raise httpx.ConnectError("không phân giải được tên máy", request=request) from None
            ds = []
            for x in tt:
                ip = ipaddress.ip_address(x[4][0])
                if ip not in ds:
                    ds.append(ip)
        chon = None
        ly_do = None
        for ip in ds:
            ly_do = self._kiem_ip(ip)
            if ly_do is None:
                chon = ip
                break
        if chon is None:
            # Lý do (kèm IP) chỉ vào nhật ký máy chủ, không vào lỗi trả cho khách gọi API.
            logging.getLogger("web2api.chuyen_tiep").warning("Chặn kết nối tới %s: phân giải ra %s — %s",
                                                              may, ds[0] if ds else "?", ly_do)
            raise LoiDichBiChan("đích trỏ vào dải bị chặn lúc kết nối", request=request)
        if not la_ip_chu:
            request.url = request.url.copy_with(host=str(chon))
            if request.url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": may}
        return await self._goc.handle_async_request(request)

    async def aclose(self) -> None:
        await self._goc.aclose()


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
        phan_loai_loi: Callable[[int, dict, dict], str | None] | None = None,
        kha_nang: tuple[str, ...] = (),
        goi_y_tu_choi_kha_nang: str = "",
        kiem_ip: Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], str | None] | None = None,
    ):
        self.ten = ten
        self.tien_to = tien_to
        self.base_url = base_url.rstrip("/")
        # Không khoá (dịch vụ trong LAN không đòi) = một "khe" rỗng, không gửi Authorization.
        self.khoa = list(khoa) or [""]
        self._doi_id_model = doi_id_model
        self._phan_loai_loi = phan_loai_loi
        self._vi_tri = 0
        self._nghi: dict[str, float] = {}
        # Khoá đã trả 200 cho chat trong thể hiện này — căn cứ phân xử 400 (xem «Phân loại lỗi» ở đầu tệp).
        self._chat_duoc: set[str] = set()
        # Căn cứ phân xử 404 (cùng chỗ): cặp (khoá, model) đã trả 200 cho chat, và sổ «model không có ở khoá này»
        # {(khoá, model): hết hạn (epoch)}.
        self._model_duoc: set[tuple[str, str]] = set()
        self._khong_co: dict[tuple[str, str], float] = {}
        # Khả năng tốn tiền (ảnh, video…) mà suc_khoe báo theo từng khoá; trạng thái theo (khoá, khả năng):
        # {"dung_duoc": bool, "ma": mã HTTP nhà cung cấp trả, "den": hết nghỉ (epoch, 0 = không nghỉ)}.
        self.kha_nang = tuple(kha_nang)
        # Lời giải thích khi khoá bị từ chối một khả năng (vd «hạng miễn phí không có») — riêng của nhà cung cấp,
        # nên MODULE truyền vào; lõi không biết nhà cung cấp nào.
        self._goi_y_tu_choi_kn = goi_y_tu_choi_kha_nang.strip()
        self._kn: dict[tuple[str, str], dict] = {}
        # Mã HTTP khiến khoá nghỉ chat (429 = hết hạn mức; 401/403/400 = bị từ chối) — `_nghi` chỉ giữ giờ hết nghỉ.
        self._nghi_ma: dict[str, int] = {}
        # Bộ đếm dùng từng khoá (xem «BỘ ĐẾM DÙNG TỪNG KHOÁ» ở đầu tệp).
        self._dem: dict[str, dict] = {}
        self._dang_dung: str | None = None
        self._dem_models: list[dict] | None = None
        self._dem_luc = 0.0
        self._khach: httpx.AsyncClient | None = None
        # Hàm kiểm IP lúc KẾT NỐI (chống DNS rebinding): nhận ipaddress, trả None nếu qua / chuỗi lý do nếu chặn.
        # None = không ghim — đích do module tự cố định (không phải URL người dùng khai) nên không cần.
        self._kiem_ip = kiem_ip
        self._dong_ho: Callable[[], float] = time.time
        self.log = logging.getLogger(f"web2api.chuyen_tiep.{tien_to}")

    # ── tiện ích ──

    def _client(self) -> httpx.AsyncClient:
        # Tạo muộn, BÊN TRONG vòng sự kiện đang chạy: AsyncClient gắn với vòng lặp tạo ra nó.
        if self._khach is None:
            self._khach = httpx.AsyncClient(
                follow_redirects=False,
                transport=TransportGhimIp(self._kiem_ip) if self._kiem_ip is not None else None)
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

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str:
        """Phân loại lỗi từ hook của module hoặc phân loại mặc định. Mã lạ/ngoại lệ → 'khong_ro'."""
        ket_qua = None
        if self._phan_loai_loi is not None:
            try:
                ket_qua = self._phan_loai_loi(ma, loi, boi_canh)
            except Exception as e:
                self.log.warning("%s: hook phan_loai_loi ném lỗi (%s), coi là 'khong_ro'", self.ten, type(e).__name__)
                ket_qua = "khong_ro"
        if ket_qua is None:
            ket_qua = phan_loai_loi_mac_dinh(ma)
        if not isinstance(ket_qua, str) or ket_qua not in LOAI_LOI_CHUAN:
            return "khong_ro"
        return ket_qua

    def chuan_hoa_loi(self, ma: int, noi_dung: bytes) -> dict:
        """Như `loi_va_loai` nhưng chỉ lấy lỗi (module có thể đã gắn gợi ý vào thông điệp)."""
        return self.loi_va_loai(ma, noi_dung, {})[0]

    def loi_va_loai(self, ma: int, noi_dung: bytes, boi_canh: dict) -> tuple[dict, str]:
        """Mọi dạng lỗi thượng nguồn → ({"error": {"message", "type", "code"}} đã che khoá, loại lỗi chuẩn)."""
        try:
            j = json.loads(noi_dung)
        except ValueError:
            j = None
        if isinstance(j, list) and j and isinstance(j[0], dict):
            j = j[0]  # Một số nhà cung cấp OpenAI-compat đôi khi bọc lỗi trong MẢNG
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
        loi = {"error": {"message": tin, "type": loai_theo_ma(ma), "code": code}}
        return loi, self.phan_loai_loi(ma, loi, boi_canh)

    # ── chat ──

    def doi_model(self, body: dict) -> dict:
        than = dict(body)  # nông: mọi trường khác giữ nguyên đối tượng, không lọc
        model = than.get("model")
        if isinstance(model, str) and model.startswith(self.tien_to + "/"):
            than["model"] = model[len(self.tien_to) + 1:]
        return than

    def _ghi_dung(self, khoa: str, viec: str, ma: int) -> None:
        """Chỗ ghi DUY NHẤT của bộ đếm dùng từng khoá: `viec` = "chat" hoặc mã khả năng, `ma` = mã HTTP nhà cung cấp trả."""
        d = self._dem.setdefault(khoa, {"luot": {}, "so_429": 0, "dung_cuoi": None, "lan_429_cuoi": None})
        bay_gio = self._dong_ho()
        if 200 <= ma < 300:
            d["luot"][viec] = d["luot"].get(viec, 0) + 1
            d["dung_cuoi"] = bay_gio
            self._dang_dung = khoa
        elif ma == 429:
            d["so_429"] += 1
            d["lan_429_cuoi"] = bay_gio

    def _han_muc(self, khoa: str, bay_gio: float) -> list[dict]:
        """Hạn mức theo việc của MỘT khoá (hợp đồng `han_muc` ở app/kham_pha.py), dựng từ dữ liệu đã có — không dò chữ.
        Trạng thái: «het_han_muc» = đang nghỉ vì 429 sau khi đã dùng được; «bi_tu_choi» = 401/403/400 bị đánh dấu, hoặc
        429 với khả năng CHƯA từng dùng được (cách `goi_kha_nang` đã phân loại); «dung_duoc»; «chua_ro» = chưa dùng thật."""
        luot = self._dem.get(khoa, {}).get("luot", {})
        ra = []
        den = self._nghi.get(khoa, 0.0)
        if den > bay_gio:
            ma = self._nghi_ma.get(khoa)
            tt = {"trang_thai": "het_han_muc" if ma == 429 else "bi_tu_choi", "ma": ma, "hoi_luc": iso(den)}
        else:
            tt = {"trang_thai": "dung_duoc" if luot.get("chat") else "chua_ro", "ma": None, "hoi_luc": None}
        ra.append({"ten": "chat", "da_dung": luot.get("chat", 0), "toi_da": None, **tt})
        for kn in self.kha_nang:
            k = self._kn.get((khoa, kn))
            if k is not None and k["den"] > bay_gio:
                het = k["ma"] == 429 and k["dung_duoc"]
                tt = {"trang_thai": "het_han_muc" if het else "bi_tu_choi", "ma": k["ma"], "hoi_luc": iso(k["den"])}
            else:
                tt = {"trang_thai": "dung_duoc" if k is not None and k["dung_duoc"] else "chua_ro", "ma": None, "hoi_luc": None}
            ra.append({"ten": kn, "da_dung": luot.get(kn, 0), "toi_da": None, **tt})
        return ra

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

        model = str(than.get("model"))
        # Cặp (khoá, model) trong sổ «model không có» bị bỏ qua — trừ khi chỉ còn chúng (đừng chết cứng).
        ung_vien = [k for k in ung_vien if self._khong_co.get((k, model), 0.0) <= bay_gio] or ung_vien

        timeout = httpx.Timeout(self.TIMEOUT_DOC_STREAM if stream else self.TIMEOUT_DOC, connect=self.TIMEOUT_KET_NOI)
        url = f"{self.base_url}/chat/completions"
        may = urlsplit(url).netloc
        loi_cuoi: KetQuaChat | None = None
        loi_so_sanh: KetQuaChat | None = None  # lỗi 400/404 gần nhất của khoá chưa rõ
        # Khoá chưa rõ trả 400 (hỏng nếu một khoá sau trả 200 cho CÙNG thân yêu cầu) / trả 404 (model không có ở nó).
        nghi_ngo: list[str] = []
        khong_co: list[str] = []
        da_thu_lai = False
        for khoa in ung_vien:
            khach = self._client()
            req = khach.build_request("POST", url, json=than, headers=self._tieu_de(khoa), timeout=timeout)
            try:
                r = await khach.send(req, stream=True)
            except LoiDichBiChan:  # đích bị chặn lúc nối: mọi khoá sẽ hỏng y hệt, không thử tiếp
                return tra_loi_json(502, {"error": {"message": f"{self.ten} ({may}): đích trỏ vào dải bị chặn lúc kết nối",
                                                    "type": "api_error", "code": "ssrf_blocked"}})
            except httpx.TimeoutException as e:
                loi_cuoi = tra_loi_json(504, {"error": {"message": f"{self.ten} ({may}) không trả lời kịp: {type(e).__name__}",
                                                        "type": "api_error", "code": "timeout"}})
            except httpx.HTTPError as e:
                loi_cuoi = tra_loi_json(502, {"error": {"message": f"Không kết nối được {self.ten} ({may}): {type(e).__name__}",
                                                        "type": "api_error", "code": "upstream_unreachable"}})
            else:
                if r.status_code == 200:
                    # Mã 200 đã tới, chưa byte nào gửi khách: đủ để kết luận về khoá, kể cả khi stream.
                    self._chat_duoc.add(khoa)
                    self._ghi_dung(khoa, "chat", 200)
                    self._model_duoc.add((khoa, model))
                    self._khong_co.pop((khoa, model), None)
                    for k in nghi_ngo:
                        self._nghi[k] = bay_gio + NGHI_KHOA_HONG
                        self._nghi_ma[k] = 400
                        # Chỉ dạng che của khoá — không ghi thân lỗi.
                        self.log.warning("%s: khoá %s bị coi là hỏng (400 trong khi khoá khác trả 200), nghỉ %.0f s",
                                         self.ten, self.nhan_khoa(k), NGHI_KHOA_HONG)
                    for k in khong_co:
                        self._khong_co[(k, model)] = bay_gio + NGHI_MODEL_KHONG_CO
                        self.log.warning("%s: khoá %s không có model %s (404 trong khi khoá khác trả 200), bỏ qua "
                                         "khoá này cho model đó %.0f s", self.ten, self.nhan_khoa(k), model,
                                         NGHI_MODEL_KHONG_CO)
                    hd = tieu_de_cho_khach(r, "application/json")
                    if stream:
                        return 200, hd, self._chuyen_dong(r)
                    try:
                        noi_dung = await r.aread()
                    finally:
                        await r.aclose()
                    return 200, hd, noi_dung
                try:
                    noi_dung = await r.aread()
                finally:
                    await r.aclose()
                ma = r.status_code
                loi, loai = self.loi_va_loai(ma, noi_dung, {"viec": "chat", "model": model, "khoa": khoa})
                loi_cuoi = tra_loi_json(ma, loi)
                tin = loi["error"]["message"]
                if loai == "chan_vung":
                    return loi_cuoi
                if loai == "het_han_muc":
                    nghi = doc_retry_after(r.headers.get("retry-after"), bay_gio)
                    self._nghi[khoa] = bay_gio + nghi
                    self._nghi_ma[khoa] = 429
                    self._ghi_dung(khoa, "chat", 429)
                    self.log.warning("%s: khoá %s hết hạn mức (429), nghỉ %.0f s", self.ten, self.nhan_khoa(khoa), nghi)
                    continue
                if loai == "khoa_hong":
                    self._nghi[khoa] = bay_gio + NGHI_KHOA_HONG
                    self._nghi_ma[khoa] = ma
                    self._chat_duoc.discard(khoa)
                    self.log.warning("%s: khoá %s bị từ chối (%s), nghỉ %.0f s: %s",
                                     self.ten, self.nhan_khoa(khoa), ma, NGHI_KHOA_HONG, tin[:200])
                    continue
                if loai == "khong_co_o_khoa":
                    self._khong_co[(khoa, model)] = bay_gio + NGHI_MODEL_KHONG_CO
                    self.log.warning("%s: khoá %s không có model %s, bỏ qua %.0f s",
                                     self.ten, self.nhan_khoa(khoa), model, NGHI_MODEL_KHONG_CO)
                    continue
                if loai == "yeu_cau_sai":
                    return loi_cuoi
                if loai == "khong_ro":
                    if ma == 400:
                        if khoa not in self._chat_duoc:
                            nghi_ngo.append(khoa)
                            loi_so_sanh = loi_cuoi
                            continue
                        # Khoá từng chạy tốt → lỗi của yêu cầu; về «chưa rõ» để lần 400 sau được phân xử.
                        self._chat_duoc.discard(khoa)
                    elif ma == 404:
                        if (khoa, model) not in self._model_duoc:
                            khong_co.append(khoa)
                            loi_so_sanh = loi_cuoi
                            continue
                        # Cặp từng chạy tốt → lỗi của yêu cầu; về «chưa rõ» để lần 404 sau được phân xử.
                        self._model_duoc.discard((khoa, model))
                    elif ma < 500:
                        return loi_cuoi
                if loai != "may_chu_loi" and ma < 500:
                    return loi_cuoi
            # 5xx hoặc lỗi mạng: thử thêm đúng một khoá.
            if da_thu_lai:
                break
            da_thu_lai = True
        assert loi_cuoi is not None
        if loi_cuoi[0] < 500 and loi_so_sanh is not None:
            # Mọi khoá đã thử đều 400/404 (hoặc khoá còn lại bị từ chối/hết hạn mức): không đánh dấu, không ghi sổ.
            return loi_so_sanh
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

    async def noi(self, body: dict) -> KetQuaChat:
        """POST /v1/audio/speech — chuyển tiếp JSON OpenAI, trả âm thanh nhị phân stream từng khúc. Xoay khoá theo
        (khoá, "noi") như khả năng tốn tiền (`goi_kha_nang`): khoá không có quyền giọng nói không làm chat nghỉ."""
        _, kq = await self.goi_kha_nang("noi", "đọc văn bản thành giọng nói", "POST", f"{self.base_url}/audio/speech",
                                        json_than=self.doi_model(body), tieu_de=self._tieu_de, stream=True,
                                        kieu_mac_dinh="audio/mpeg")
        return kq

    async def chuyen_multipart(self, endpoint: str, kha_nang: str, viec: str, yc: YeuCauDaPhan) -> KetQuaChat:
        """Gửi yêu cầu multipart tới `{base_url}/{endpoint}`: dựng data/files đã làm sạch tên tệp và MIME type, xoay khoá
        theo (khoá, `kha_nang`) qua `goi_kha_nang`."""
        truong = dict(yc.truong)
        model = truong.get("model")
        if isinstance(model, str) and model.startswith(self.tien_to + "/"):
            truong["model"] = model[len(self.tien_to) + 1:]

        data = {}
        for k, v in truong.items():
            if isinstance(v, list):
                data[k] = [str(item) for item in v]
            else:
                data[k] = str(v)

        files = []
        for t in yc.tep:
            ten_tep = lam_sach_ten_tep(t.ten_tep, mac_dinh=f"{t.ten_truong}.bin")
            ct = lam_sach_content_type(t.content_type)
            files.append((t.ten_truong, (ten_tep, t.noi_dung, ct)))

        _, kq = await self.goi_kha_nang(kha_nang, viec, "POST", f"{self.base_url}/{endpoint}", data=data, files=files,
                                        tieu_de=self._tieu_de)
        return kq

    async def nghe(self, yc: YeuCauDaPhan) -> tuple[int, dict, bytes]:
        """POST /v1/audio/transcriptions (multipart) — chuyển nguyên tới dịch vụ ở xa."""
        return await self.chuyen_multipart("audio/transcriptions", "nghe", "nhận dạng giọng nói", yc)

    async def anh_sua(self, yc: YeuCauDaPhan) -> tuple[int, dict, bytes]:
        """POST /v1/images/edits (multipart) — chuyển nguyên tới dịch vụ ở xa."""
        return await self.chuyen_multipart("images/edits", "anh_sua", "sửa ảnh", yc)

    # ── khả năng tốn tiền (ảnh, video…) — xem «KHẢ NĂNG TỐN TIỀN» ở đầu tệp ──

    def _ghi_kn(self, khoa: str, kha_nang: str, **gia_tri) -> None:
        self._kn.setdefault((khoa, kha_nang), {"dung_duoc": False, "ma": None, "den": 0.0}).update(gia_tri)

    def trang_thai_kha_nang(self, khoa: str, kha_nang: str, bay_gio: float) -> dict:
        tt = self._kn.get((khoa, kha_nang))
        if tt is not None and tt["den"] > bay_gio:
            return {"trang_thai": "bi_tu_choi", "ma": tt["ma"], "thu_lai_luc": iso(tt["den"])}
        return {"trang_thai": "dung_duoc" if tt is not None and tt["dung_duoc"] else "chua_ro"}

    def _loi_het_khoa(self, kha_nang: str, viec: str, bay_gio: float) -> KetQuaChat:
        """Mọi khoá đang bị từ chối khả năng này: nêu mã nhà cung cấp trả cho từng khoá (dạng che). Chỉ khi mọi khoá là khoá ĐÃ
        dùng được và chỉ hết hạn mức (429) → 429 kèm Retry-After như chat; còn lại → 403."""
        cho = [(k, self._kn[(k, kha_nang)]) for k in self.khoa
               if self._kn.get((k, kha_nang), {}).get("den", 0.0) > bay_gio]
        chi_tiet = "; ".join(f"{self.nhan_khoa(k)}: HTTP {tt['ma']}, thử lại lúc {iso(tt['den'])}" for k, tt in cho)
        chi_429 = bool(cho) and all(tt["ma"] == 429 and tt["dung_duoc"] for _, tt in cho)
        ma = 429 if chi_429 else 403
        goi_y = f"{self._goi_y_tu_choi_kn} " if self._goi_y_tu_choi_kn else ""
        tin = (f"Không khoá nào trong {len(self.khoa)} khoá của {self.ten} được phép {viec} — {chi_tiet}. "
               f"{goi_y}Khoá bị từ chối được thử lại sau 24 giờ hoặc khi lưu lại cấu hình module; "
               "khoá từng dùng được mà hết hạn mức (429) thì thử lại theo Retry-After.")
        hd = {"retry-after": str(max(1, int(min(tt["den"] for _, tt in cho) - bay_gio + 0.999)))} if chi_429 else {}
        loi = {"error": {"message": tin, "type": loai_theo_ma(ma),
                         "code": "rate_limit_exceeded" if chi_429 else "no_key_permitted"}}
        return tra_loi_json(ma, loi, hd)

    async def goi_kha_nang(self, kha_nang: str, viec: str, phuong_thuc: str, url: str, *, json_than=None,
                           data: dict | None = None, files: list | None = None, tieu_de: Callable[[str], dict],
                           stream: bool = False, kieu_mac_dinh: str = "application/json",
                           ) -> tuple[str | None, KetQuaChat]:
        """Gọi một khả năng tốn tiền, xoay khoá theo (khoá, khả năng). Trả (khoá đã thành công, kết quả đã đọc hết —
        hoặc `LuongNhiPhan` nếu `stream`) hoặc (None, lỗi chuẩn OpenAI đã che khoá). Thân là `json_than` hoặc
        multipart `data`/`files`. `tieu_de(khoa)` dựng header mang khoá — khoá không vào URL. `stream` chỉ đọc trước
        mã trạng thái: đổi khoá chỉ xảy ra TRƯỚC byte đầu gửi khách."""
        bay_gio = self._dong_ho()
        n = len(self.khoa)
        dau = self._vi_tri % n
        self._vi_tri = (dau + 1) % n
        ung_vien = [self.khoa[(dau + i) % n] for i in range(n)]
        ung_vien = [k for k in ung_vien if self._kn.get((k, kha_nang), {}).get("den", 0.0) <= bay_gio]
        if not ung_vien:
            return None, self._loi_het_khoa(kha_nang, viec, bay_gio)
        timeout = httpx.Timeout(self.TIMEOUT_DOC_STREAM if stream else self.TIMEOUT_DOC, connect=self.TIMEOUT_KET_NOI)
        may = urlsplit(url).netloc
        loi_cuoi: KetQuaChat | None = None
        loi_400: dict | None = None
        nghi_ngo: list[str] = []  # khoá chưa rõ trả 400 — bị từ chối nếu một khoá sau thành công
        da_thu_lai = False
        for khoa in ung_vien:
            khach = self._client()
            req = khach.build_request(phuong_thuc, url, json=json_than, data=data, files=files, headers=tieu_de(khoa),
                                      timeout=timeout)
            try:
                r = await khach.send(req, stream=stream)
            except LoiDichBiChan:  # như chat(): trả ngay, không đánh dấu khoá nào
                return None, tra_loi_json(502, {"error": {
                    "message": f"{self.ten} ({may}): đích trỏ vào dải bị chặn lúc kết nối",
                    "type": "api_error", "code": "ssrf_blocked"}})
            except httpx.TimeoutException as e:
                loi = {"error": {"message": f"{self.ten} ({may}) không trả lời kịp: {type(e).__name__}",
                                 "type": "api_error", "code": "timeout"}}
                loi_cuoi = tra_loi_json(504, loi)
            except httpx.HTTPError as e:
                loi = {"error": {"message": f"Không kết nối được {self.ten} ({may}): {type(e).__name__}",
                                 "type": "api_error", "code": "upstream_unreachable"}}
                loi_cuoi = tra_loi_json(502, loi)
            else:
                ma = r.status_code
                if stream and not 200 <= ma < 300:
                    try:
                        await r.aread()
                    finally:
                        await r.aclose()
                if 200 <= ma < 300:
                    self._ghi_kn(khoa, kha_nang, dung_duoc=True, ma=None, den=0.0)
                    self._ghi_dung(khoa, kha_nang, ma)
                    for k in nghi_ngo:  # cùng thân yêu cầu, khoá này được còn khoá kia 400 → lỗi của khoá kia
                        self._ghi_kn(k, kha_nang, dung_duoc=False, ma=400, den=bay_gio + NGHI_KHA_NANG)
                        self.log.warning("%s: khoá %s bị từ chối %s (400 trong khi khoá khác được), nghỉ %.0f s",
                                         self.ten, self.nhan_khoa(k), viec, NGHI_KHA_NANG)
                    hd = tieu_de_cho_khach(r, kieu_mac_dinh)
                    return khoa, (ma, hd, LuongNhiPhan(r, self.log, self.ten) if stream else r.content)
                loi, loai = self.loi_va_loai(ma, r.content, {"viec": viec, "kha_nang": kha_nang, "khoa": khoa})
                loi_cuoi = tra_loi_json(ma, loi)
                tin = loi["error"]["message"]
                if loai == "chan_vung":  # chặn vùng: lỗi của máy chủ, mọi khoá sẽ hỏng y hệt
                    return None, loi_cuoi
                if loai in ("khoa_hong", "khong_co_o_khoa"):
                    self._ghi_kn(khoa, kha_nang, dung_duoc=False, ma=ma, den=bay_gio + NGHI_KHA_NANG)
                    self.log.warning("%s: khoá %s bị từ chối %s (%s), nghỉ %.0f s cho riêng việc này: %s",
                                     self.ten, self.nhan_khoa(khoa), viec, ma, NGHI_KHA_NANG, tin[:200])
                    continue
                if loai == "het_han_muc":
                    da_duoc = self._kn.get((khoa, kha_nang), {}).get("dung_duoc", False)
                    nghi = doc_retry_after(r.headers.get("retry-after"), bay_gio) if da_duoc else NGHI_KHA_NANG
                    self._ghi_kn(khoa, kha_nang, ma=429, den=bay_gio + nghi)
                    self._ghi_dung(khoa, kha_nang, 429)
                    self.log.warning("%s: khoá %s hết hạn mức %s (429), nghỉ %.0f s", self.ten,
                                     self.nhan_khoa(khoa), viec, nghi)
                    continue
                if loai == "khong_ro":
                    if ma == 400 and not self._kn.get((khoa, kha_nang), {}).get("dung_duoc"):
                        nghi_ngo.append(khoa)
                        loi_400 = loi
                        continue
                    if ma < 500:
                        return None, loi_cuoi
                if loai == "yeu_cau_sai":
                    return None, loi_cuoi
                if loai != "may_chu_loi" and ma < 500:
                    return None, loi_cuoi
            # 5xx hoặc lỗi mạng: thử thêm đúng một khoá.
            if da_thu_lai:
                break
            da_thu_lai = True
        assert loi_cuoi is not None
        if loi_cuoi[0] >= 500:
            return None, loi_cuoi
        if loi_400 is not None:
            loi_400["error"]["message"] += (f" — {self.ten} trả 400 cho mọi khoá đã thử ({len(nghi_ngo)}): nếu yêu "
                                            "cầu đúng thì có thể không khoá nào được dùng tính năng này."
                                            + (f" {self._goi_y_tu_choi_kn}" if self._goi_y_tu_choi_kn else ""))
            return None, tra_loi_json(400, loi_400)
        return None, self._loi_het_khoa(kha_nang, viec, bay_gio)

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
        for i, (k, muc) in enumerate(zip(self.khoa, ket_qua)):
            den = self._nghi.get(k, 0.0)
            muc["dang_nghi_den"] = iso(den) if den > bay_gio else None
            if self.kha_nang:
                muc["kha_nang"] = {kn: self.trang_thai_kha_nang(k, kn, bay_gio) for kn in self.kha_nang}
            # Bộ đếm dùng (kể từ lần khởi động / lưu cấu hình gần nhất) — chỉ số và giờ, không khoá.
            d = self._dem.get(k, {})
            muc["thu_tu"] = i + 1
            muc["dang_dung"] = self._dang_dung is not None and k == self._dang_dung
            muc["dung_cuoi"] = iso(d["dung_cuoi"]) if d.get("dung_cuoi") is not None else None
            muc["so_429"] = d.get("so_429", 0)
            muc["lan_429_cuoi"] = iso(d["lan_429_cuoi"]) if d.get("lan_429_cuoi") is not None else None
            muc["han_muc"] = self._han_muc(k, bay_gio)
        return {"ok": any(m["ok"] for m in ket_qua), "base_url": self.base_url, "khoa": list(ket_qua)}

    async def dung(self) -> None:
        if self._khach is not None:
            khach, self._khach = self._khach, None
            await khach.aclose()
