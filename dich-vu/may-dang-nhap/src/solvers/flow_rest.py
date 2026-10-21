# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/solvers/flow_rest.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Các hợp đồng Flow REST và điểm vào tạo ảnh/video.

CẬP NHẬT 07/09/2026: `tao_anh` dùng RPC cookie của flow.google.com qua
`flow_rpc.py`. Endpoint labs.google/fx/api/auth/session cũ trả OAuth hết hạn
sau khi Flow chuyển ứng dụng. Ghi chú bên dưới mô tả đường REST cũ; video
và các hàm dựng/đọc hợp đồng REST vẫn giữ nguyên.

VÌ SAO CÓ FILE NÀY. Đường đang chạy (`flow_google.py`) điều khiển GIAO DIỆN Flow
bằng Chrome: gõ prompt vào Slate editor, bấm dropdown model, bấm nút Tạo, rình
mạng lấy kết quả. Mỗi lượt giữ `browser_pool` 60–300 giây, và vỡ mỗi lần Flow
đổi giao diện — sự cố 08/08/2026 là một yêu cầu tạo ẢNH bấm hụt dropdown rồi
trừ mất tín dụng VIDEO Omni Flash 8 giây.

File này gửi thẳng JSON tới đúng API mà giao diện đó gọi. Trình duyệt còn đúng
hai việc, mỗi việc khoảng một giây: xin `access_token` và đúc token reCAPTCHA.
Không còn DOM, nên không còn cả lớp lỗi "bấm hụt".

CHẠY THẬT LẦN ĐẦU 09/08/2026: `POST /v1/google/flow/rest/generate-image` trong
container trả **HTTP 200 sau 35 giây**, kèm `mediaId` thật. Đây là câu trả lời
cho ẩn số để ngỏ suốt quá trình: token reCAPTCHA đúc trong trình duyệt của
container DÙNG ĐƯỢC cho request phát từ chính tiến trình đó, dù request không đi
qua ngữ cảnh trang. Kiến trúc "trình duyệt làm máy phát token" chạy được.

Đáp của lệnh tạo chỉ có `mediaId`, KHÔNG có link ảnh — link phải đổi thêm một
lượt qua `media.getMediaUrlRedirect`; xem `lay_link_media()`.

ĐO THẬT 09/08/2026, tài khoản `google-nguoi-a`, project xxxxxxxx-…:

  * Tiến trình Python thuần — không cookie, không `x-browser-validation`,
    không `x-client-data` — gọi tới `aisandbox-pa.googleapis.com` KHÔNG bị chặn
    ở tầng vận chuyển. `httpx` mặc định và `curl_cffi(impersonate="chrome")` trả
    kết quả giống hệt nhau, nên dấu vân tay TLS không phải yếu tố. Ghi chú trong
    `services/image_providers/flow_google.py` nói "any non-browser caller bị từ
    chối" là sai ở phần này.
  * Cửa duy nhất là reCAPTCHA. Thiếu token thì 403 "reCAPTCHA evaluation failed".
  * `imageModelName` được kiểm TRƯỚC reCAPTCHA nên đo được trực tiếp:

        GEM_PIX_2        hợp lệ   (đi tiếp tới cửa reCAPTCHA)
        NARWHAL          hợp lệ   (đi tiếp tới cửa reCAPTCHA)
        HARBOR_SEAL      hợp lệ   (đi tiếp tới cửa reCAPTCHA)
        GEM_PIX          404      có thật nhưng tài khoản này không có
        IMAGEN_3_5       404      có thật nhưng tài khoản này không có
        NANO_BANANA_PRO  400      INVALID_ARGUMENT — KHÔNG phải hằng số có thật
        IMAGEN_4         400      INVALID_ARGUMENT — KHÔNG phải hằng số có thật
        GEM_PIX_3        400      INVALID_ARGUMENT — chưa tồn tại

KHỚP VỚI QUẢN LÝ MODEL — RỒI, từ 09/08/2026. Bảng ánh xạ hiện tại, đã đo:

        flow/banana-pro     → GEM_PIX_2
        flow/auto           → GEM_PIX_2
        flow/banana-2       → NARWHAL
        flow/banana-2-lite  → HARBOR_SEAL

`flow/imagen-4` đã bỏ khỏi Quản lý Model. `NANO_BANANA_PRO` — tên mà
`flow/banana-pro` trỏ vào suốt thời gian trước — hoá ra chưa bao giờ là hằng số
có thật; đường DOM che mất vì nó chỉ dùng chuỗi đó làm khoá tra NHÃN dropdown
chứ không gửi xuống API. `kiem_model_anh()` chặn tại chỗ kèm thông báo nêu tên
đúng, phòng cấu hình cũ còn sót.

MODEL VIDEO — quét đủ họ 09/08/2026, cả hai tỷ lệ khung hình:

  * Chỉ họ t2v có đủ bốn bậc (Quality / Fast / Lite / Lite ưu tiên thấp).
  * Ba chế độ có ảnh — i2v, interpolation, r2v — KHÔNG có bản Fast hay Quality
    (đều 404), nhưng mỗi chế độ có HAI bản Lite: `_lite` và `_lite_low_priority`.
  * Omni Flash: 4s, 6s, 8s, 10s — có ở t2v, i2v VÀ r2v. Không có 5s, không có
    12s. Riêng chế độ ảnh-đầu-và-cuối thì KHÔNG có Omni Flash
    (`abra_interpolation_*` không tồn tại).
  * Tỷ lệ khung hình KHÔNG đổi tính hợp lệ của tên model — mọi khoá đều qua ở cả
    16:9 lẫn 9:16. Nếu tỷ lệ có ràng buộc thì nó nằm sau cửa reCAPTCHA, chỗ chưa
    đo miễn phí được.

ĐƯỜNG VIDEO GỬI TỪ TRONG TRANG, KHÔNG DÙNG httpx. Đường ảnh gửi bằng httpx
trần và chạy tốt; đường video cùng token, cùng reCAPTCHA hợp lệ, model hợp lệ,
đường dẫn đúng — vẫn 403 "The caller does not have permission". Đã loại trừ
từng khả năng bằng phép đo (hết quyền, sai bậc trả phí, sai đường dẫn, sai tên
model — xem `_goi_video_trong_trang`). Còn lại đúng một khác biệt: request của
đường DOM phát ra TỪ TRONG TRANG nên mang origin/referer/sec-fetch và header
riêng của Chrome. CHƯA XÁC NHẬN đây là nguyên nhân — cần một lượt deploy nữa.

ĐƯỜNG VIDEO — đo 09/08/2026, ba trong bốn endpoint đạt ngay, một sai:

  * `batchAsyncGenerateVideoText`, `…StartImage`, `…ReferenceImages` đều đạt.
  * `…StartAndEndImage` đòi CẢ HAI ảnh. Thiếu `endImage` là 400 "Request
    contains an invalid argument" — không nói thiếu trường nào.
    `cropCoordinates` thì tuỳ chọn: có hay không, số nguyên hay số thực đều qua.
  * Khoá model hỏng ở đường video trả **404**, không phải 400 như đường ảnh, nên
    rất dễ tưởng là lỗi tài khoản. Đo được: `abra_t2v_5s`, `abra_r2v_5s`,
    `veo_3_1_r2v`, `veo_3_1_r2v_fast` đều 404. Omni Flash chỉ có 4s/6s/8s —
    và 5 giây từng là mặc định khi bên gọi không nêu thời lượng, tức nhánh mặc
    định của Omni Flash trước đây trỏ thẳng vào model không tồn tại.
  * `upload_anh_tham_chieu()` đã chạy thật và trả về mediaId hợp lệ.

NGUỒN THÂN YÊU CẦU. Dựng theo bản gỡ rối của VEO3 AI Studio 1.08 (ứng dụng
Electron gọi cùng API này). Những trường chưa đo được trực tiếp — hằng số tỷ lệ
khung hình — đều ghi rõ là "theo bản gỡ rối, chưa đo".

CHƯA LÀM: nâng ảnh (`flow/upsampleImage`), sửa video (`…EditVideo`), và giọng
đọc trong chế độ thành phần (`referenceAudio`). Thêm sau khi cần, theo đúng
khuôn ở đây.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_HOST = "https://aisandbox-pa.googleapis.com"
TOOL = "PINHOLE"

# Token OAuth của Flow sống khoảng một giờ (đo: hạn 2026-08-09T22:24:30Z, lấy
# lúc ~21:2x). Cache ngắn hơn hẳn để không bao giờ dùng token sắp hết hạn.
_TOKEN_SONG_S = 45 * 60
_token_cache: dict[str, tuple[str, float]] = {}

# Các tên đã đo là KHÔNG tồn tại. Danh sách này chỉ để chặn cái đã biết sai —
# tên lạ vẫn cho đi qua, vì Flow thêm model mới liên tục và chặn cứng sẽ khoá
# mất model mới ngay ngày nó ra.
MODEL_ANH_DA_DO_LA_SAI = {
    "NANO_BANANA_PRO": "400 INVALID_ARGUMENT (đo 09/08/2026), thay bằng GEM_PIX_2",
    "IMAGEN_4": "400 INVALID_ARGUMENT (đo 09/08/2026)",
    "GEM_PIX_3": "400 INVALID_ARGUMENT (đo 09/08/2026), chưa tồn tại",
}
# Thứ tự có nghĩa: phần tử đầu là model mạnh nhất, dùng khi bên gọi không nêu tên.
MODEL_ANH_DA_DO_LA_DUNG = ("GEM_PIX_2", "NARWHAL", "HARBOR_SEAL")

# Tỷ lệ khung hình ảnh — theo bản gỡ rối VEO3 AI Studio 1.08, CHƯA đo trực tiếp
# (API kiểm trường này sau cửa reCAPTCHA nên phép đo hôm 09/08 chưa chạm tới).
# Lưu ý: dạng chữ ở đây là FOUR_THREE / THREE_FOUR, khác dạng 4_3 / 3_4 mà
# `services/image_providers/flow_google.py` đang dùng. Bên đó chỉ dùng chuỗi làm
# khoá tra nhãn dropdown nên chưa bao giờ tới API; đừng đồng bộ hai bảng cho tới
# khi đo xong bên nào đúng.
TY_LE_ANH = {
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "4:3": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    "3:4": "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR",
}
TY_LE_VIDEO = {
    "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
}

# Bốn chế độ video và endpoint tương ứng.
CHE_DO_VIDEO = {
    "text_to_video": "batchAsyncGenerateVideoText",
    "image_start": "batchAsyncGenerateVideoStartImage",
    "image_start_end": "batchAsyncGenerateVideoStartAndEndImage",
    "component": "batchAsyncGenerateVideoReferenceImages",
}

# Khoá model video theo CHẾ ĐỘ và nhãn giao diện. QUÉT ĐỦ HỌ 09/08/2026 trên
# `v1/video:batchAsyncGenerateVideoText`, cả hai tỷ lệ khung hình.
#
# Tên model bị kiểm TRƯỚC cả reCAPTCHA lẫn quyền nên quét được miễn phí: 403 là
# có, 404 là không dùng được. Khác đường ảnh — ở đó tên sai trả 400.
#
# Hai điều quét ra mà bản gỡ rối không nói:
#
#   * BA chế độ có ảnh (i2v, interpolation, r2v) KHÔNG hề có bản Fast hay
#     Quality — `veo_3_1_i2v`, `veo_3_1_i2v_fast`, `veo_3_1_interpolation`,
#     `veo_3_1_interpolation_fast`, `veo_3_1_r2v`, `veo_3_1_r2v_fast` đều 404.
#     Chỉ họ t2v mới đủ bốn bậc.
#   * Nhưng mỗi chế độ đó có HAI bản Lite: `_lite` và `_lite_low_priority`.
#     Ứng dụng gốc gán cứng bản ưu tiên thấp, ta bê theo — nên người chọn
#     "Veo 3.1 - Lite" vẫn bị đẩy xuống hàng chờ chậm mà không biết. Nay tách ra.
#
# Tỷ lệ khung hình KHÔNG đổi tính hợp lệ của tên model: mọi khoá đều qua ở cả
# 16:9 lẫn 9:16. Nếu tỷ lệ có ràng buộc thì nó nằm sau cửa reCAPTCHA, chưa đo được.
#
# Omni Flash: 4s, 6s, 8s, 10s đều có ở CẢ t2v lẫn r2v. Không có 5s, không có 12s.
NHAN_OMNI = "Omni Flash"
NHAN_LITE = "Veo 3.1 - Lite"
NHAN_LITE_CHO = "Veo 3.1 - Lite [Lower Priority]"
NHAN_FAST = "Veo 3.1 - Fast"
NHAN_QUALITY = "Veo 3.1 - Quality"

# CẢNH BÁO về các bậc `[Lower Priority]`: khoá `*_low_priority` có thật trên API
# nhưng tài khoản của ta KHÔNG được dùng — Google trả 403 "The caller does not
# have permission" (đo 10/08/2026, xem chú thích ở `NHAN_MAC_DINH`). Giữ chúng
# trong bảng vì tên là tên thật và tài khoản gói cao hơn có thể dùng được, nhưng
# ĐỪNG đặt làm mặc định. Ai chọn bậc đó sẽ nhận 403 nói về quyền.
MODEL_VIDEO: dict[str, dict[str, str]] = {
    "text_to_video": {
        NHAN_OMNI: "abra_t2v_{giay}s",
        NHAN_LITE: "veo_3_1_t2v_lite",
        NHAN_LITE_CHO: "veo_3_1_t2v_lite_low_priority",
        NHAN_FAST: "veo_3_1_t2v_fast",
        NHAN_QUALITY: "veo_3_1_t2v",
    },
    "component": {
        # KHÔNG có bậc Fast lẫn Quality, và đây không phải chuyện chưa tìm ra
        # tên khoá: chọn Quality rồi thêm ảnh thì chính giao diện Flow chặn lại
        # — "Bạn không thể dùng thành phần hình ảnh với mô hình này"
        # (đo 10/08/2026). Bậc cao của Veo 3.1 không nhận ảnh tham chiếu.
        NHAN_OMNI: "abra_r2v_{giay}s",
        NHAN_LITE: "veo_3_1_r2v_lite",
        NHAN_LITE_CHO: "veo_3_1_r2v_lite_low_priority",
    },
    "image_start": {
        # Omni Flash CÓ ở chế độ này (abra_i2v_4/6/8/10s) — đo 10/08/2026.
        NHAN_OMNI: "abra_i2v_{giay}s",
        # Hai bậc cao đi theo họ `_s`, và chỉ chúng có bản dọc. Bậc Fast từng
        # bị khai nhầm là "không có" vì tôi dò họ KHÔNG có `_s`
        # (`veo_3_1_i2v_fast` → 404); đo lại đúng họ thì nó có — 10/08/2026.
        NHAN_QUALITY: "veo_3_1_i2v_s",
        NHAN_FAST: "veo_3_1_i2v_s_fast",
        NHAN_LITE: "veo_3_1_i2v_lite",
        NHAN_LITE_CHO: "veo_3_1_i2v_lite_low_priority",
    },
    "image_start_end": {
        # `_fl` = first-last. Payload thật của giao diện dùng
        # `veo_3_1_i2v_s_portrait_fl` — cả một họ khoá mà không cách nào đoán ra
        # từ bản gỡ rối; họ `interpolation_*` cũng có thật nhưng chỉ bậc Lite.
        #
        # KHÔNG có Omni Flash ở đây: giao diện Flow báo thẳng "Mô hình này không
        # hỗ trợ khung hình kết thúc" (10/08/2026), và quả thật không khoá
        # `abra_*` nào nhận hậu tố `_fl`.
        NHAN_QUALITY: "veo_3_1_i2v_s_fl",
        NHAN_FAST: "veo_3_1_i2v_s_fast_fl",
        NHAN_LITE: "veo_3_1_interpolation_lite",
        NHAN_LITE_CHO: "veo_3_1_interpolation_lite_low_priority",
    },
}
# 9:16 DÙNG KHOÁ MODEL KHÁC, không phải chỉ đổi `aspectRatio`.
#
# BA BẢN CHỤP THẬT 10/08/2026 từ giao diện Flow ở chế độ dọc, mỗi bản một bậc —
# cả ba đều đọc được `generatedVideo.model` trong đáp, tức tên Google THẬT SỰ
# dùng, không phải nhãn ta tự đặt:
#
#     Veo 3.1 - Quality  ->  "veo_3_1_t2v_portrait"
#     Veo 3.1 - Fast     ->  "veo_3_1_t2v_fast_portrait"
#     Veo 3.1 - Lite     ->  "veo_3_1_t2v_lite"        (khoá THƯỜNG, không đổi)
#
# Cả ba lượt đều kèm `aspectRatio: VIDEO_ASPECT_RATIO_PORTRAIT` và đáp trả
# `generatedVideo.aspectRatio = VIDEO_ASPECT_RATIO_PORTRAIT` — tức Lite ra được
# video dọc bằng chính khoá thường.
#
# Quét API khớp: có bản `_portrait` là hai khoá t2v bậc cao và hai khoá thuộc
# họ `i2v_s` (kể cả bản `_fl`). Mọi bản Lite, cả họ `interpolation_*` lẫn
# `r2v_*`, và Omni Flash đều KHÔNG có — `veo_3_1_t2v_lite_portrait` và
# `veo_3_1_r2v_lite_portrait` đều trả 404.
#
# Vị trí hậu tố có quy tắc: `_portrait` đứng SAU `_fast` và TRƯỚC `_fl`. Đảo
# thứ tự là 404 (`veo_3_1_i2v_s_portrait_fast` không tồn tại).
#
# Với những khoá không có bản dọc thì gửi khoá thường kèm
# `aspectRatio: VIDEO_ASPECT_RATIO_PORTRAIT` — cách đó qua được vòng kiểm tham
# số, nhưng CHƯA đo được là nó có ra video dọc thật hay không. Ai đụng tới chỗ
# này nhớ đo lại bằng cách xem `video.generatedVideo.aspectRatio` trong đáp.
KHOA_DOC = {
    "veo_3_1_t2v": "veo_3_1_t2v_portrait",
    "veo_3_1_t2v_fast": "veo_3_1_t2v_fast_portrait",
    # Họ i2v bậc chuẩn: `_portrait` chèn TRƯỚC hậu tố `_fl`, không phải nối đuôi.
    "veo_3_1_i2v_s": "veo_3_1_i2v_s_portrait",
    "veo_3_1_i2v_s_fl": "veo_3_1_i2v_s_portrait_fl",
    "veo_3_1_i2v_s_fast": "veo_3_1_i2v_s_fast_portrait",
    "veo_3_1_i2v_s_fast_fl": "veo_3_1_i2v_s_fast_portrait_fl",
}


def khoa_theo_ty_le(khoa: str, ty_le: str) -> str:
    """Đổi sang bản dọc khi cần. 16:9 giữ nguyên."""
    if ty_le == "9:16":
        return KHOA_DOC.get(khoa, khoa)
    return khoa


# Nhãn dùng khi bên gọi không nêu.
#
# TỪNG là `NHAN_LITE_CHO` (bản ưu tiên thấp, rẻ nhất) và đó là NGUYÊN NHÂN của
# cái 403 chặn đường video suốt 09-10/08/2026. Các khoá `*_low_priority` CÓ THẬT
# nhưng tài khoản của ta KHÔNG ĐƯỢC DÙNG, và Google từ chối bằng đúng câu
# "The caller does not have permission". Đo trên tài khoản Main, giữ nguyên mọi
# thứ khác, chỉ đổi một trường:
#
#     veo_3_1_t2v_lite_low_priority  ->  403 The caller does not have permission
#     veo_3_1_t2v_lite               ->  200, và ra MP4 thật 2,1 MB
#
# BÀI HỌC ĐẮT: phép dò tên model (gửi KHÔNG kèm reCAPTCHA rồi đọc mã trả về) coi
# 403 là "tên hợp lệ". Sai — ở đó có HAI loại 403 khác nhau hẳn: "tên này Google
# biết" và "tên này bạn không được dùng". Cả hai đều 403 nên phép dò không phân
# biệt được, và tôi đã kết luận `_low_priority` dùng được trong khi nó bị chặn.
# Ai dò thêm khoá mới nhớ đọc THÔNG ĐIỆP, đừng chỉ đọc mã số.
NHAN_MAC_DINH = NHAN_LITE
MODEL_VIDEO_MAC_DINH = {m: b[NHAN_MAC_DINH] for m, b in MODEL_VIDEO.items()}
# Mọi nhãn giao diện, để phân biệt "nhãn lạ" với "nhãn có thật nhưng chế độ này
# không có" — hai thứ cần thông báo khác nhau.
MOI_NHAN = {NHAN_OMNI, NHAN_LITE, NHAN_LITE_CHO, NHAN_FAST, NHAN_QUALITY}

# Thời lượng Omni Flash có thật, đo cho cả t2v và r2v. 5 giây KHÔNG có, mà 5 lại
# từng là mặc định khi bên gọi không nêu thời lượng.
GIAY_OMNI_FLASH = (4, 6, 8, 10)
GIAY_OMNI_FLASH_MAC_DINH = 8

# Đường VIDEO có gửi `userPaygateTier`, và giá trị đúng là ONE — đọc được từ
# telemetry thật của giao diện Flow 09/08/2026 (MEDIA_GENERATION_PAYGATE_TIER).
# Bản gỡ rối ghi TIER_TWO và ta bê nguyên sang: 403 "The caller does not have
# permission". Bỏ hẳn trường cũng 403. Đường ẢNH thì ngược lại — bản chụp
# request thật cho thấy ảnh KHÔNG gửi trường này.
TIER_VIDEO = "PAYGATE_TIER_ONE"

# Toạ độ cắt. Ta gửi phủ trọn khung, tức "dùng cả ảnh, không cắt".
#
# Giao diện Flow thì KHÔNG làm vậy: payload thật 10/08/2026 gửi
#     startImage.cropCoordinates = {top:0, left:0.3954, bottom:1, right:0.6046}
# — nó tự cắt giữa để ảnh nguồn (4040×1324, rất ngang) khớp khung dọc 9:16.
#
# Phủ trọn khung là hằng số hợp lệ (đo: có hay không, số nguyên hay số thực đều
# qua), nên đây không phải lỗi — chỉ là bố cục khác. Muốn giống giao diện thì
# phải đọc kích thước ảnh nguồn rồi tính cắt giữa theo tỷ lệ đích; chưa làm vì
# chưa có nhu cầu và vì đoán công thức là cách hỏng thầm lặng.
_CAT_TRON_KHUNG = {"top": 0, "left": 0, "bottom": 1, "right": 1}


class LoiFlowRest(RuntimeError):
    """Lỗi từ API Flow, giữ nguyên mã HTTP để bên gọi phân biệt được.

    429 và lỗi hết ngạch phải phân biệt được với hỏng thật, vì bên
    `services/image_providers/flow_google.py` dựa vào chuỗi thông báo để quyết
    định cho tài khoản nghỉ (cooldown) hay đẩy xuống cuối danh sách.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


# ── Dựng thân yêu cầu (hàm thuần, không I/O — test bám vào đây) ────────────

def kiem_model_anh(model: str) -> str:
    """Trả tên model ảnh, ném lỗi nếu là tên ĐÃ ĐO là không tồn tại."""
    ten = (model or "").strip().upper()
    if not ten:
        return MODEL_ANH_DA_DO_LA_DUNG[0]
    if ten in MODEL_ANH_DA_DO_LA_SAI:
        raise ValueError(
            f"'{ten}' không phải model ảnh có thật của Flow — API trả "
            f"{MODEL_ANH_DA_DO_LA_SAI[ten]}. Tên đã đo là dùng được: "
            f"{', '.join(MODEL_ANH_DA_DO_LA_DUNG)}."
        )
    return ten


def _ngu_canh(project_id: str, session_id: str,
              recaptcha: str | None, tier: str | None = None) -> dict[str, Any]:
    """`clientContext` — dựng theo BẢN CHỤP REQUEST THẬT của giao diện Flow
    (09/08/2026), không phải theo bản gỡ rối nữa.

    Client thật gửi ĐÚNG bốn trường: projectId, tool, sessionId, recaptchaContext.
    KHÔNG có `userPaygateTier`. Bản gỡ rối VEO3 AI Studio có trường đó
    (PAYGATE_TIER_ONE cho ảnh, TIER_TWO cho video) và ta bê nguyên sang — đường
    ảnh may mắn được Google bỏ qua, còn đường video trả thẳng 403 "The caller
    does not have permission". Khai một bậc trả phí mà tài khoản không có thì bị
    từ chối, hợp lý.

    `sessionId` có dấu chấm phẩy đứng trước, kể cả ở đường ảnh — bản chụp cho
    thấy ";1786253707634". Trước đây ta gửi ảnh không có dấu này.
    """
    ctx: dict[str, Any] = {
        "projectId": project_id,
        "tool": TOOL,
        "sessionId": session_id,
    }
    if tier:
        ctx["userPaygateTier"] = tier
    if recaptcha:
        ctx["recaptchaContext"] = {
            "token": recaptcha,
            "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
        }
    return ctx


def than_tao_anh(
    *,
    project_id: str,
    prompt: str,
    model: str,
    aspect_ratio: str = "16:9",
    count: int = 1,
    media_ids: list[str] | None = None,
    recaptcha: str | None = None,
    session_id: str | None = None,
    batch_id: str | None = None,
    seeds: list[int] | None = None,
) -> dict[str, Any]:
    """Thân cho `v1/projects/{id}/flowMedia:batchGenerateImages`.

    `count` ảnh được tạo bằng cách nhân bản phần tử trong `requests`, mỗi bản
    một seed riêng — đúng cách app làm, và tiết kiệm hơn hẳn cách hiện tại là
    bắn `count` lời gọi song song qua giao diện.
    """
    ten_model = kiem_model_anh(model)
    sid = session_id or (";" + str(int(time.time() * 1000)))
    ctx = _ngu_canh(project_id, sid, recaptcha)
    so = max(1, int(count))
    hat = seeds or [random.randint(0, 99998) for _ in range(so)]
    yeu_cau = [
        {
            "clientContext": dict(ctx),
            "imageAspectRatio": TY_LE_ANH.get(aspect_ratio, aspect_ratio),
            "structuredPrompt": {"parts": [{"text": prompt}]},
            "imageModelName": ten_model,
            "seed": hat[i % len(hat)],
            "imageInputs": [
                {"imageInputType": "IMAGE_INPUT_TYPE_REFERENCE", "name": m}
                for m in (media_ids or [])
            ],
        }
        for i in range(so)
    ]
    return {
        "clientContext": dict(ctx),
        "mediaGenerationContext": {"batchId": batch_id or str(uuid.uuid4())},
        "useNewMedia": True,
        "requests": yeu_cau,
    }


def tach_prompt_theo_anh(prompt: str, anh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chế độ "thành phần": cắt prompt thành các mảnh text xen ảnh tham chiếu.

    Tên file (bỏ đuôi) chính là từ khoá tìm trong prompt. Ảnh nào tên không xuất
    hiện trong prompt thì KHÔNG có mảnh `reference` nào trỏ tới — nó chỉ nằm ở
    `referenceImages`. Đây là hành vi của app, và cũng là chỗ người dùng hay
    hiểu nhầm: đặt tên file không khớp prompt thì ảnh gần như không tác dụng.

    Mỗi `mediaId` chỉ được nhắc một lần dù tên xuất hiện nhiều lần.
    """
    moc: list[dict[str, str]] = []
    for a in anh:
        ten_tep = str(a.get("name") or a.get("fileName") or "")
        tu_khoa = re.sub(r"\.[^/.]+$", "", ten_tep)
        if tu_khoa and a.get("mediaId"):
            moc.append({"token": tu_khoa, "handle": ten_tep, "mediaId": str(a["mediaId"])})
    if not moc:
        return [{"text": prompt}]

    # Tên dài trước, để "Lan Anh" không bị "Lan" cắt mất.
    moc.sort(key=lambda m: len(m["token"]), reverse=True)
    mau = re.compile(r"\b(" + "|".join(re.escape(m["token"]) for m in moc) + r")\b",
                     re.IGNORECASE)

    phan: list[dict[str, Any]] = []
    da_dung: set[str] = set()
    vi_tri = 0
    for khop in mau.finditer(prompt):
        m = next((x for x in moc if x["token"].lower() == khop.group(1).lower()), None)
        if m is None or m["mediaId"] in da_dung:
            continue
        if khop.start() > vi_tri:
            phan.append({"text": prompt[vi_tri:khop.start()]})
        phan.append({"reference": {"media": {"handle": m["handle"],
                                             "mediaId": m["mediaId"]}}})
        da_dung.add(m["mediaId"])
        vi_tri = khop.end()
    if vi_tri < len(prompt):
        phan.append({"text": prompt[vi_tri:]})
    return phan or [{"text": prompt}]


def chon_model_video(che_do: str, nhan: str | None, duration: str | None,
                     ty_le: str = "16:9") -> str:
    """Khoá model theo CHẾ ĐỘ + nhãn giao diện.

    Không chế độ nào có đủ năm bậc (xem bảng `MODEL_VIDEO`): Thành phần không
    có Fast lẫn Quality, còn ảnh đầu+cuối thì không có Omni Flash. Xin một bậc
    mà chế độ đó không có là ném lỗi nêu rõ chế độ này có gì — thay vì lặng lẽ
    hạ xuống Lite. Người chọn Quality mà nhận Lite là đúng kiểu hỏng đã gây sự
    cố 08/08/2026, chỉ khác chiều.

    Nhãn LẠ (không nằm trong bộ nhãn giao diện) thì vẫn rơi về mặc định, để cấu
    hình cũ hoặc bên gọi tự do không bị chặn cứng.
    """
    bang = MODEL_VIDEO.get(che_do)
    if bang is None:
        raise ValueError(f"chế độ video lạ: {che_do!r}; chỉ có {', '.join(MODEL_VIDEO)}")
    ten = nhan or NHAN_MAC_DINH
    if ten not in bang:
        if ten in MOI_NHAN:
            raise ValueError(
                f"chế độ {che_do} không có bản {ten!r}. Bản dùng được: "
                f"{', '.join(sorted(bang))}."
            )
        ten = NHAN_MAC_DINH
    khoa = bang[ten]
    if "{giay}" in khoa:
        so = re.sub(r"[^0-9]", "", str(duration or ""))
        giay = int(so) if so else GIAY_OMNI_FLASH_MAC_DINH
        if giay not in GIAY_OMNI_FLASH:
            raise ValueError(
                f"Omni Flash không có bản {giay} giây. Thời lượng có thật: "
                f"{', '.join(f'{g}s' for g in GIAY_OMNI_FLASH)}."
            )
        khoa = khoa.format(giay=giay)
    return khoa_theo_ty_le(khoa, ty_le)


def than_tao_video(
    *,
    project_id: str,
    prompt: str,
    che_do: str = "text_to_video",
    model_key: str,
    aspect_ratio: str = "16:9",
    count: int = 1,
    anh: list[dict[str, Any]] | None = None,
    recaptcha: str | None = None,
    session_id: str | None = None,
    batch_id: str | None = None,
    seeds: list[int] | None = None,
    tier: str = TIER_VIDEO,
) -> dict[str, Any]:
    """Thân cho `v1/video:batchAsyncGenerateVideo*`. Xem `CHE_DO_VIDEO` để biết
    chế độ nào đi tới endpoint nào."""
    if che_do not in CHE_DO_VIDEO:
        raise ValueError(f"chế độ video lạ: {che_do!r}; "
                         f"chỉ có {', '.join(CHE_DO_VIDEO)}")
    anh = anh or []
    # ĐO 09/08/2026: endpoint …StartAndEndImage đòi CẢ HAI ảnh. Thiếu `endImage`
    # thì trả 400 "Request contains an invalid argument" — không nói thiếu
    # trường nào. (`cropCoordinates` thì tuỳ chọn: có hay không, số nguyên hay
    # số thực, đều qua.) Ba chế độ còn lại có ảnh cũng phải có ít nhất một, nếu
    # không thân yêu cầu thiếu hẳn trường ảnh và Google trả đúng 400 đó.
    can_it_nhat = {"image_start": 1, "image_start_end": 2, "component": 1}.get(che_do, 0)
    if len(anh) < can_it_nhat:
        raise ValueError(
            f"chế độ {che_do} cần {can_it_nhat} ảnh, mới nhận được {len(anh)}. "
            f"{'Ảnh đầu và ảnh cuối đều bắt buộc.' if che_do == 'image_start_end' else ''}"
        )
    sid = session_id or (";" + str(int(time.time() * 1000)))
    ctx = _ngu_canh(project_id, sid, recaptcha, tier)

    goc: dict[str, Any] = {
        "aspectRatio": TY_LE_VIDEO.get(aspect_ratio, "VIDEO_ASPECT_RATIO_LANDSCAPE"),
        "videoModelKey": model_key,
        "metadata": {},
    }
    if che_do == "component":
        goc["textInput"] = {"structuredPrompt": {"parts": tach_prompt_theo_anh(prompt, anh)}}
        goc["referenceImages"] = [
            {"mediaId": a["mediaId"], "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
            for a in anh if a.get("mediaId")
        ]
    else:
        goc["textInput"] = {"structuredPrompt": {"parts": [{"text": prompt}]}}
        if che_do == "image_start":
            goc["startImage"] = {"mediaId": anh[0]["mediaId"]}
        elif che_do == "image_start_end":
            # Số lượng ảnh đã được chốt ở đầu hàm nên khỏi phải thử lại ở đây.
            # `cropCoordinates` là tuỳ chọn (đo 09/08) — giữ lại vì app gốc gửi,
            # và nó nói rõ ý định "dùng trọn khung, không cắt".
            goc["startImage"] = {"mediaId": anh[0]["mediaId"],
                                 "cropCoordinates": dict(_CAT_TRON_KHUNG)}
            goc["endImage"] = {"mediaId": anh[1]["mediaId"],
                               "cropCoordinates": dict(_CAT_TRON_KHUNG)}

    so = max(1, int(count))
    hat = seeds or [random.randint(0, 99998) for _ in range(so)]
    return {
        "mediaGenerationContext": {
            "batchId": batch_id or str(uuid.uuid4()),
            # Payload thật của giao diện gửi BLOCK_SILENCED_VIDEOS: thà hỏng
            # còn hơn trả về video câm. Bản gỡ rối VEO3 ghi RETURN_SILENCED_VIDEOS
            # — nghĩa NGƯỢC LẠI — và ta bê theo suốt. Cả hai đều là hằng số hợp
            # lệ nên không có gì báo sai; chỉ khác ở chỗ người dùng nhận được gì
            # khi Google dựng hụt phần tiếng.
            "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
        },
        "clientContext": dict(ctx),
        "requests": [{**goc, "seed": hat[i % len(hat)]} for i in range(so)],
        "useV2ModelConfig": True,
    }


def doc_gen_ids(dap: dict[str, Any]) -> list[str]:
    """Lấy ID theo dõi từ đáp của lệnh tạo video.

    App ưu tiên `metadata.primaryMediaId`, lùi về `name`. Giữ thứ tự và bỏ trùng.
    """
    ra: list[str] = []
    for wf in (dap.get("workflows") or []):
        ma = ((wf.get("metadata") or {}).get("primaryMediaId")) or wf.get("name")
        if ma and ma not in ra:
            ra.append(str(ma))
    return ra


def doc_link_anh(dap: dict[str, Any]) -> list[str]:
    """Link ảnh nằm ở `media[].image.generatedImage.fifeUrl`.

    Bản chụp request thật 09/08/2026 cho thấy đáp của lệnh tạo ĐÃ kèm link, nên
    không phải đi đổi id lấy link nữa. Trước đây ta tưởng không có, vì dò regex
    trên `str(dap)` — mà `str()` của dict Python dùng NHÁY ĐƠN, còn regex thì
    tìm nháy kép, nên không bao giờ khớp. Đọc theo cấu trúc thay vì dò chuỗi.

    Link đã ký (`Expires` + `Signature`) và CDN trả `access-control-allow-origin: *`
    nên tải được bằng HTTP thường, không cần cookie hay bearer.
    """
    ra: list[str] = []
    for m in (dap.get("media") or []):
        u = ((m.get("image") or {}).get("generatedImage") or {}).get("fifeUrl")
        if u and u not in ra:
            ra.append(str(u))
    return ra


def gom_ket_qua(trang_thai: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Gộp bảng tiến độ thành (danh sách item xong, danh sách lý do hỏng).

    Hình dạng item phải là `{"url": ...}` vì `api/veo_video.py` đọc thẳng
    `data[0]["url"]` và `_luu_thu_vien()` cũng dựa vào đó để tải video về thư
    viện. Đổi tên trường ở đây là làm hỏng cả hai chỗ mà không ai báo.
    """
    xong: list[dict[str, Any]] = []
    hong: list[str] = []
    for gen_id, tt in trang_thai.items():
        if tt.get("status") == "COMPLETED" and tt.get("url"):
            xong.append({"url": tt["url"], "id": gen_id})
        elif tt.get("status") == "FAILED":
            hong.append(f"{gen_id}: {tt.get('reason') or 'không rõ lý do'}")
    return xong, hong


def con_dang_chay(trang_thai: dict[str, dict[str, Any]]) -> bool:
    """Còn ID nào chưa ngã ngũ không (chưa COMPLETED và chưa FAILED)."""
    return any(tt.get("status") not in ("COMPLETED", "FAILED")
               for tt in trang_thai.values())


def doc_trang_thai(dap: dict[str, Any], gen_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Chuẩn hoá đáp của lệnh tra tiến độ thành {gen_id: {status, url, reason}}.

    `url` CÓ THỂ RỖNG dù đã xong. Bản chụp đáp thật 10/08/2026 của
    `batchCheckAsyncVideoGenerationStatus` không hề có trường `videoUrl` — đáp
    chỉ mang `mediaMetadata.mediaStatus`, `video.generatedVideo` và
    `video.operation`. Giao diện Flow lấy link bằng một lượt riêng qua
    `media.getMediaUrlRedirect`, đúng như đường ẢNH.

    Bản gỡ rối VEO3 AI Studio có đọc `videoUrl` nên vẫn giữ nhánh đó phòng khi
    Flow trả về; thiếu thì bên gọi phải tự đi đổi id lấy link —
    `trang_thai_video()` làm việc đó.
    """
    ra = {g: {"status": "PROCESSING", "url": None, "reason": None} for g in gen_ids}
    for m in (dap.get("media") or []):
        ten = m.get("name")
        if not ten or ten not in ra:
            continue
        tt = ((m.get("mediaMetadata") or {}).get("mediaStatus") or {})
        trang_thai = str(tt.get("mediaGenerationStatus") or "")
        if "SUCCESSFUL" in trang_thai or "COMPLETED" in trang_thai:
            ra[ten] = {"status": "COMPLETED", "url": m.get("videoUrl"), "reason": None}
        elif "FAILED" in trang_thai:
            ra[ten] = {
                "status": "FAILED",
                "url": None,
                "reason": tt.get("failureReason") or tt.get("errorMessage")
                or "Google báo thất bại, không nêu lý do",
            }
    return ra


# ── Trình duyệt: chỉ để lấy token ─────────────────────────────────────────

_JS_LAY_TOKEN = (
    "(async()=>{try{const r=await fetch('/fx/api/auth/session');"
    "const d=await r.json();return d.access_token||null;}catch(e){return null;}})()"
)


async def _bearer_tu_trang(page, profile: str) -> str:
    cache = _token_cache.get(profile)
    if cache and cache[1] > time.time():
        return cache[0]
    if "labs.google" not in (page.url or ""):
        await page.goto("https://labs.google/fx/tools/flow",
                        wait_until="domcontentloaded", timeout=30_000)
    token = await page.evaluate(_JS_LAY_TOKEN)
    if not token:
        # Lùi về cách cũ: rình header Authorization của chính trang.
        from .flow_google import _capture_bearer
        token = await _capture_bearer(page, timeout_s=25.0)
    _token_cache[profile] = (str(token), time.time() + _TOKEN_SONG_S)
    return str(token)


async def lay_bearer(profile: str, headless: bool = True) -> str:
    """`access_token` của Flow. Còn hạn trong cache thì KHÔNG mở trình duyệt.

    Tách khỏi việc đúc reCAPTCHA vì hai thứ có tuổi thọ chênh nhau rất xa: token
    này sống ~1 giờ, token reCAPTCHA sống ~2 phút.
    """
    cache = _token_cache.get(profile)
    if cache and cache[1] > time.time():
        return cache[0]
    from ..browser_pool import pool
    async with pool.page(profile=profile, headless=headless) as page:
        return await _bearer_tu_trang(page, profile)


_JS_LINK_MEDIA = """
async (ten_media) => {
  const ra = {};
  for (const ten of ten_media) {
    try {
      const r = await fetch(
        'https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=' +
        encodeURIComponent(ten));
      ra[ten] = r.ok ? r.url : null;
    } catch (e) { ra[ten] = null; }
  }
  return ra;
}
"""


async def lay_link_media(profile: str, ten_media: list[str],
                         headless: bool = True) -> dict[str, str]:
    """`mediaId` → link CDN đã ký. Cần trình duyệt vì trpc dựa vào cookie.

    Đáp của lệnh tạo ảnh chỉ có `mediaId`, KHÔNG có link (đo 09/08/2026 trên
    lượt tạo thật đầu tiên). Link lấy qua `media.getMediaUrlRedirect` — đúng
    endpoint mà ứng dụng gốc dùng cho video, hoá ra dùng được cho cả ảnh: nó
    chuyển hướng sang `https://flow-content.google/image/<id>?Expires=…&
    Signature=…` và thân là JPEG thật.

    Link đã ký nên tải được từ bất kỳ đâu, không cần cookie — bên gọi cứ HTTP
    thường mà lấy. Chỉ riêng bước ĐỔI id lấy link là cần phiên đăng nhập.

    Gom cả danh sách vào MỘT lượt mở trang: mở trình duyệt là phần đắt nhất,
    còn mỗi lượt fetch bên trong chỉ vài chục mili giây.
    """
    if not ten_media:
        return {}
    from ..browser_pool import pool
    async with pool.page(profile=profile, headless=headless) as page:
        if "labs.google" not in (page.url or ""):
            await page.goto("https://labs.google/fx/tools/flow",
                            wait_until="domcontentloaded", timeout=30_000)
        ra = await page.evaluate(_JS_LINK_MEDIA, list(ten_media))
    return {k: v for k, v in (ra or {}).items() if v}


_JS_POST = """
async ({url, bearer, body}) => {
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Authorization': 'Bearer ' + bearer,
                'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    let data;
    try { data = await r.json(); } catch (e) { data = await r.text(); }
    return {status: r.status, data};
  } catch (e) { return {status: 0, data: String(e)}; }
}
"""


async def _goi_video_trong_trang(profile: str, headless: bool, url: str,
                                 dung_than, action: str) -> dict[str, Any]:
    """Đúc reCAPTCHA và GỬI request video NGAY TRONG TRANG, một lượt mở duy nhất.

    VÌ SAO KHÔNG DÙNG httpx NHƯ ĐƯỜNG ẢNH. Đo 09/08/2026: đường ẢNH gửi bằng
    httpx trần (không cookie, không header riêng của Chrome) chạy tốt. Đường
    VIDEO thì cùng token, cùng reCAPTCHA hợp lệ, model hợp lệ, endpoint đúng —
    vẫn trả 403 "The caller does not have permission". Đã loại trừ:

        * hết quyền tạo video   — đường DOM tạo được video thật trên chính tài
                                  khoản đó (200, MP4 2,1 MB)
        * sai bậc trả phí       — thử TIER_TWO, bỏ hẳn trường, TIER_ONE: cả ba
                                  đều 403
        * sai đường dẫn         — mọi biến thể có project đều 404, chỉ
                                  `/v1/video:*` là có thật
        * sai tên model         — quét đủ họ, khoá dùng là khoá đã đo là có

    Còn lại đúng một khác biệt: request của đường DOM phát ra TỪ TRONG TRANG nên
    mang `origin: https://labs.google`, `referer`, `sec-fetch-*` và các header
    riêng của Chrome. Đường video nhiều khả năng kiểm những thứ đó, còn đường
    ảnh thì không. Gửi trong trang là cách duy nhất có được chúng mà không phải
    giả mạo từng cái.

    `dung_than(recaptcha)` là hàm dựng thân yêu cầu — nhận token vừa đúc, vì
    token chỉ sống khoảng hai phút nên phải gắn ngay trước khi gửi.
    """
    from ..browser_pool import pool
    from .flow_google import _get_recaptcha_token

    async with pool.page(profile=profile, headless=headless) as page:
        if "labs.google" not in (page.url or ""):
            await page.goto("https://labs.google/fx/tools/flow",
                            wait_until="domcontentloaded", timeout=30_000)
        bearer = await _bearer_tu_trang(page, profile)
        recaptcha, sitekey = await _get_recaptcha_token(page, action=action)
        logger.info("flow_rest gửi video trong trang profile=%s sitekey=%s",
                    profile, sitekey[:16])
        kq = await page.evaluate(_JS_POST, {
            "url": url, "bearer": bearer, "body": dung_than(recaptcha)})

    ma = int((kq or {}).get("status") or 0)
    du_lieu = (kq or {}).get("data")
    if ma != 200:
        msg = du_lieu
        if isinstance(du_lieu, dict):
            msg = (du_lieu.get("error") or {}).get("message") or str(du_lieu)[:200]
        raise LoiFlowRest(ma or 502, str(msg)[:300])
    if not isinstance(du_lieu, dict):
        raise LoiFlowRest(502, f"đáp video không phải JSON: {str(du_lieu)[:200]}")
    return du_lieu


async def _lay_recaptcha(profile: str, headless: bool, action: str) -> str:
    """Đúc token reCAPTCHA. LUÔN phải mở trang, và phải gọi NGAY TRƯỚC lệnh gửi.

    Token sống khoảng hai phút. Nếu đúc trước rồi mới đi đẩy ảnh tham chiếu lên
    (mỗi ảnh một lời gọi HTTP) thì với bộ ảnh lớn, token có thể hết hạn đúng lúc
    gửi lệnh tạo — hỏng với thông báo "reCAPTCHA evaluation failed" trông y hệt
    lỗi mất phiên, rất khó lần ra. Vì vậy thứ tự bắt buộc là: lấy bearer → đẩy
    ảnh → đúc reCAPTCHA → gửi lệnh. Ứng dụng gốc cũng làm đúng thứ tự này.
    """
    from ..browser_pool import pool
    from .flow_google import _get_recaptcha_token

    async with pool.page(profile=profile, headless=headless) as page:
        if "labs.google" not in (page.url or ""):
            await page.goto("https://labs.google/fx/tools/flow",
                            wait_until="domcontentloaded", timeout=30_000)
        token, sitekey = await _get_recaptcha_token(page, action=action)
    logger.info("flow_rest đúc reCAPTCHA profile=%s sitekey=%s", profile, sitekey[:16])
    return token


# ── Gọi API ───────────────────────────────────────────────────────────────

def _thong_bao_loi(r: httpx.Response) -> str:
    try:
        return str((r.json().get("error") or {}).get("message") or r.text[:200])
    except ValueError:
        return r.text[:200]


async def _post(url: str, bearer: str, than: dict[str, Any], timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=than, headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        })
    if r.status_code != 200:
        raise LoiFlowRest(r.status_code, _thong_bao_loi(r))
    return r.json()


async def upload_anh_tham_chieu(*, bearer: str, project_id: str, du_lieu: bytes,
                                timeout: float = 120) -> str:
    """Đẩy một ảnh lên Flow, trả `mediaId`. Gửi nguyên file, không resize."""
    dap = await _post(f"{API_HOST}/v1/flow/uploadImage", bearer, {
        "clientContext": {"projectId": project_id, "tool": TOOL},
        "imageBytes": base64.b64encode(du_lieu).decode("ascii"),
    }, timeout)
    # Đáp thật (đo 10/08/2026) đặt id ở `media.name`, KHÔNG phải `name` tầng
    # ngoài:
    #     {"media": {"name": "<mediaId>", "projectId": …, "workflowId": …, …}}
    # Bản cũ chỉ dò ba khoá tầng ngoài rồi rơi xuống nhánh regex UUID trên
    # `str(dap)` — và vô tình lấy đúng, vì `name` tình cờ đứng trước `projectId`
    # trong đáp. Google đảo thứ tự khoá một cái là ta trả về projectId làm
    # mediaId, rồi lệnh tạo video hỏng ở tận đâu đó với thông báo không liên quan.
    media = dap.get("media")
    if isinstance(media, dict) and media.get("name"):
        return str(media["name"])
    for khoa in ("name", "mediaId", "id"):
        if dap.get(khoa):
            return str(dap[khoa])
    raise LoiFlowRest(502, f"đáp upload không có mediaId: {str(dap)[:200]}")


async def tao_anh(*, profile: str, project_id: str, prompt: str,
                  model: str = "NARWHAL", aspect_ratio: str = "16:9",
                  count: int = 1, anh_tham_chieu: list[bytes] | None = None,
                  headless: bool = True, timeout: float = 180) -> dict[str, Any]:
    """Tạo ảnh qua Flow mới; giữ hợp đồng của endpoint REST hiện có."""
    from .flow_rpc import generate_image

    return await generate_image(
        profile=profile, project_id=project_id, prompt=prompt, model=model,
        aspect_ratio=aspect_ratio, count=count, anh_tham_chieu=anh_tham_chieu,
        headless=headless, timeout=timeout,
    )


async def tao_video(*, profile: str, project_id: str, prompt: str,
                    che_do: str = "text_to_video", model_label: str | None = None,
                    model_key: str | None = None, aspect_ratio: str = "16:9",
                    duration: str | None = None, count: int = 1,
                    anh: list[dict[str, Any]] | None = None,
                    headless: bool = True, timeout: float = 180,
                    cho_xong: bool = False, cho_toi_da: float = 600,
                    nhip: float = 5,
                    tier: str = TIER_VIDEO) -> dict[str, Any]:
    """Gửi lệnh tạo video. Mặc định trả ngay `gen_ids`, KHÔNG chờ video xong.

    Đường DOM hiện tại giữ một request HTTP mở suốt 300 giây và đã đo được là
    chết đúng mốc đó với thông báo rỗng. Ở đây lệnh gửi xong là trả (1-2 giây),
    hợp với hàng đợi tác vụ sẵn có.

    `cho_xong=True` thì hàm tự tra tiến độ mỗi `nhip` giây cho tới khi xong hoặc
    quá `cho_toi_da`, rồi trả thêm khoá `data` đúng hình dạng mà
    `api/veo_video.py` đang đợi. Chỉ bật khi bên gọi thật sự không có hàng đợi —
    bật mặc định là dựng lại đúng cái bẫy 300 giây vừa bỏ đi.

    `anh` là danh sách {name, mediaId} — dùng `upload_anh_tham_chieu` để lấy
    `mediaId` trước. Với chế độ "component", `name` phải là tên file vì nó chính
    là từ khoá tìm trong prompt.
    """
    bat_dau = time.time()
    khoa = (khoa_theo_ty_le(model_key, aspect_ratio) if model_key
            else chon_model_video(che_do, model_label, duration, aspect_ratio))
    dap = await _goi_video_trong_trang(
        profile, headless,
        f"{API_HOST}/v1/video:{CHE_DO_VIDEO[che_do]}",
        lambda rc: than_tao_video(
            project_id=project_id, prompt=prompt, che_do=che_do, model_key=khoa,
            aspect_ratio=aspect_ratio, count=count, anh=anh, recaptcha=rc,
            tier=tier),
        "VIDEO_GENERATION",
    )
    gen_ids = doc_gen_ids(dap)
    if not gen_ids:
        raise LoiFlowRest(502, f"đáp không có Generation ID: {str(dap)[:300]}")
    ket: dict[str, Any] = {
        "gen_ids": gen_ids,
        "project_id": project_id,
        "video_model_key": khoa,
        "che_do": che_do,
        "elapsed_ms": int((time.time() - bat_dau) * 1000),
    }
    # Số tín dụng còn lại nằm ngay trong đáp của lệnh tạo. Phải chuyển ra ngoài:
    # `api/veo_video.py` ghi nó vào cấu hình tài khoản để hiện trên giao diện, và
    # đường DOM cũ đưa nó qua `data[0].metadata.remainingCredits`. Không chuyển
    # thì phần theo dõi tín dụng chết âm thầm — vẫn chạy, chỉ là số không bao giờ
    # đổi nữa.
    if isinstance(dap.get("remainingCredits"), (int, float)):
        ket["remaining_credits"] = dap["remainingCredits"]
    if not cho_xong:
        return ket

    han = time.time() + cho_toi_da
    trang_thai: dict[str, dict[str, Any]] = {}
    while time.time() < han:
        await asyncio.sleep(nhip)
        trang_thai = await trang_thai_video(profile=profile, project_id=project_id,
                                            gen_ids=gen_ids, headless=headless)
        if not con_dang_chay(trang_thai):
            break
    xong, hong = gom_ket_qua(trang_thai)
    ket["data"] = xong
    ket["elapsed_ms"] = int((time.time() - bat_dau) * 1000)
    if hong:
        ket["that_bai"] = hong
    if not xong:
        # Không có video nào ra thì đây là hỏng thật — phải ném, nếu không bên
        # gọi nhận `data: []` rồi coi như thành công và ghi một mục rỗng vào
        # thư viện.
        if hong:
            raise LoiFlowRest(502, "Google báo thất bại: " + "; ".join(hong))
        raise LoiFlowRest(504, f"hết {int(cho_toi_da)}s chờ Google dựng video "
                               f"(gen_ids={', '.join(gen_ids)})")
    return ket


async def trang_thai_video(*, profile: str, project_id: str, gen_ids: list[str],
                           headless: bool = True, timeout: float = 60) -> dict[str, Any]:
    """Tra tiến độ. Dùng token đã cache nên thường KHÔNG mở trình duyệt.

    Lệnh này không cần reCAPTCHA (chỉ đọc), nên chừng nào token còn hạn thì cả
    vòng chờ chạy hoàn toàn bằng HTTP.
    """
    bearer = await lay_bearer(profile, headless)
    dap = await _post(f"{API_HOST}/v1/video:batchCheckAsyncVideoGenerationStatus",
                      bearer,
                      {"media": [{"name": g, "projectId": project_id} for g in gen_ids]},
                      timeout)
    tt = doc_trang_thai(dap, gen_ids)

    # Xong mà không có link thì đi đổi id lấy link — đáp thật KHÔNG kèm
    # `videoUrl` (xem `doc_trang_thai`). Chỉ mở trình duyệt khi thật sự có cái
    # cần đổi, nên vòng chờ vẫn chạy bằng HTTP cho tới đúng lượt cuối.
    thieu = [g for g, v in tt.items() if v.get("status") == "COMPLETED" and not v.get("url")]
    if thieu:
        bang = await lay_link_media(profile, thieu, headless)
        for g, u in bang.items():
            if g in tt:
                tt[g]["url"] = u
    return tt
