# Module gemini-api — Gemini API (Google AI Studio)

Module này gọi các model Gemini của Google bằng khoá API của Google AI Studio. Nó chuyển tiếp **thẳng**
tới endpoint tương thích OpenAI chính thức của Google
(`https://generativelanguage.googleapis.com/v1beta/openai`) và giữ nguyên mọi trường của yêu cầu, nên
chữ ký suy nghĩ của Gemini 3 (trường `extra_content` của lệnh gọi công cụ) đi theo mà không cần dịch.
Phần module thêm vào: xoay vòng nhiều khoá, cho khoá nghỉ khi lỗi, đổi địa chỉ gốc để vượt chặn vùng,
chuẩn hoá lỗi, danh sách model và kiểm sức khoẻ. Từ `0.2.0` có thêm **tạo ảnh** và **tạo video Veo** — chỉ
với khoá **trả phí** (xem «Tạo ảnh và video»). Tiền tố model: `gemini`; phiên bản `0.2.0`.

Tài liệu đầy đủ (cài từng bước, ảnh giao diện, bảng đã kiểm với Google thật) nằm ở mục «Module
gemini-api» trong `docs/THAM-CHIEU.md` ở gốc kho. File này là bản tóm tắt đặt ngay cạnh mã.

## Cài

Module nằm trong kho git của dự án nên cài lúc chạy, không dựng lại ảnh. Với web2api đã chạy
(`W` là địa chỉ web2api, `K` là khoá quản trị):

```bash
curl -s -H "Authorization: Bearer $K" $W/api/modules/thong-bao          # phải thấy "id": "gemini-api"
curl -s -X POST -H "Authorization: Bearer $K" $W/api/nguon-git/cai/gemini-api
```

Hoặc bấm ô gemini-api ở Hệ thống → Module của web2api rồi bấm «Cài» (bảng chi tiết hiện «Cần gì để chạy» trước
khi cài). Cài **không cần khoá API**: xong bước
này module đã nằm trên máy nhưng chưa liên kết — chưa có model trong `/v1/models`, gọi chat trả `404`
«chưa có khoá» — cho tới khi thêm khoá (dưới đây). Module cần gói Python `httpx`
(khai ở `yeu_cau_goi` trong `manifest.json`); ảnh lõi đã có sẵn gói này.

## Cấu hình

Nhập ở bước «Thêm tài khoản/khoá API» của bảng chi tiết (hoặc Hệ thống → AI → Tài khoản), hoặc bằng
`POST /api/modules/gemini-api/cai`. Liên kết xong, Studio (chat, ảnh, video — vì manifest khai `cung_cap`
`["chat", "anh", "video"]`) và Hệ thống → AI → Provider hiện ngay:

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `api_keys` | Có | Một hoặc nhiều khoá lấy ở https://aistudio.google.com/apikey (mỗi dòng một khoá; hoặc một chuỗi cách nhau bằng dấu phẩy). Lưu riêng ở `<thư mục dữ liệu>/bi_mat/gemini-api.json` quyền 0600, không API nào trả lại khoá. **Tạo ảnh và video Veo chỉ dùng được với khoá của dự án đã bật thanh toán (khoá trả phí)** — khoá miễn phí chỉ chat được. |
| `base_url` | Không | Mặc định gọi thẳng Google. Đặt địa chỉ proxy khi bị chặn vùng; thiếu `/v1beta/openai` thì module tự thêm. Phải bắt đầu bằng `http://` hoặc `https://` và có tên máy. |
| `proxy_token` | Không | Mã bí mật gửi kèm header `X-Proxy-Token` khi gọi qua proxy. **Tự đặt**: nghĩ một chuỗi bí mật bất kỳ, đặt lên Cloudflare Worker bằng `npx wrangler secret put PROXY_TOKEN` rồi dán đúng giá trị đó vào đây (xem «Chặn vùng và proxy» dưới đây). Để trống khi gọi thẳng Google. |

Lúc lưu, module gọi thử `GET /models` với **từng** khoá (không tốn token) và chỉ lưu những khoá dùng
được. Không khoá nào dùng được thì cấu hình không được lưu và API trả mã 400.

Thêm/bớt từng khoá về sau: `GET /api/modules/gemini-api/cau-hinh` trả khoá đã lưu ở dạng che theo thứ tự;
gửi `{"cau_hinh": {"api_keys": {"them": ["AIza..."]}}}` để thêm (giữ khoá cũ), `{"cau_hinh": {"api_keys":
{"giu": [0, 2]}}}` để chỉ giữ các khoá chỉ số 0 và 2. Trường vắng mặt (ví dụ `base_url`) giữ giá trị đã lưu.

```bash
curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"cau_hinh": {"api_keys": ["AIza...khoá-của-bạn"]}}' \
  $W/api/modules/gemini-api/cai
```

## Chặn vùng và proxy

Mọi đặc thù của Google (địa chỉ, header `x-goog-api-key`, API gốc Veo, nhận diện chặn vùng, lời giải thích «hạng miễn
phí không có ảnh/video» — `GOI_Y_HANG_MIEN_PHI` truyền vào bộ chuyển tiếp dùng chung) nằm trong `module.py`; lõi
không biết chữ «Google».

Ở một số nước (có thể gồm Việt Nam) Google chặn địa chỉ IP với lỗi chứa «User location is not supported».
Module nhận ra lỗi này (mã 400 hoặc 403 kèm chữ «location») và gợi ý đặt `base_url` qua proxy; lỗi chặn
vùng **không** làm khoá bị đánh dấu hỏng vì mọi khoá đều sẽ bị như nhau.

Cách vượt: dựng một Cloudflare Worker từ `deploy/cloudflare-gemini-proxy.js` ở gốc kho (Worker chuyển
nguyên đường dẫn sang `generativelanguage.googleapis.com`), cấu hình biến bí mật `PROXY_TOKEN` trên Worker
(wrangler secret put PROXY_TOKEN), rồi cài lại module với `"base_url": "https://<tên>.workers.dev"` và
`"proxy_token": "<giá_trị_token>"`. web2api sẽ gửi header `X-Proxy-Token` trong mọi lời gọi tới proxy.

## Hành vi khi gọi

- **Xoay vòng khoá**: gặp `429` thì khoá nghỉ theo `Retry-After` của Google (mặc định 60 giây, tối đa
  10 phút) rồi tự dùng lại; gặp `401`/`403` (không phải chặn vùng) thì khoá nghỉ 1 giờ; cả hai trường hợp
  module thử khoá kế. Mọi khoá đang nghỉ thì trả `429` kèm `Retry-After`.
- **Thời hạn**: Google không trả lời trong 120 giây (stream: 60 giây giữa hai khúc) thì trả `504`.
- **Model**: tên khách gọi là `gemini/<tên model>` (ví dụ `gemini/gemini-2.5-flash`); module bỏ tiền tố
  `models/` mà Google thêm vào khi liệt kê.
- **Sức khoẻ**: `GET /api/modules/gemini-api/suc-khoe` (cần khoá quản trị) kiểm từng khoá, không tốn
  token và không lộ khoá — chỉ 4 ký tự cuối.
- **Mạng ra**: `generativelanguage.googleapis.com` (hoặc máy của `base_url`).

## Tạo ảnh và video (chỉ khoá trả phí)

Theo trang giá Google ngày 11/10/2026 (https://ai.google.dev/gemini-api/docs/pricing), mọi model tạo ảnh
(`gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, `gemini-nano-banana-2.1`;
`gemini-2.5-flash-image` tắt 02/10/2026) và tạo video (`veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`,
`veo-3.1-lite-generate-preview`) ghi **«Not available»** ở hạng miễn phí. Mỗi lần tạo trên khoá trả phí là
**tốn tiền thật** — ví dụ ảnh Nano Banana 2 Lite khoảng 0,034 USD/ảnh 1K, video Veo 3.1 Fast 0,10 USD/giây
720p, Veo 3.1 Standard 0,40 USD/giây. Bảng giá đầy đủ ở mục «Module gemini-api» của `docs/THAM-CHIEU.md` gốc kho.

- **Ảnh**: `POST /v1/images/generations` của web2api → endpoint tương thích OpenAI của Google
  (`{base_url}/images/generations`), thân đi nguyên văn, chỉ bỏ tiền tố `gemini/`. Trang tương thích của Google (đọc
  12/10/2026) ghi `images.generate` dùng chính model ảnh dạng Gemini (`gemini-2.5-flash-image`,
  `gemini-3-pro-image-preview`) — đường này dựa vào tài liệu, **chưa thử** với khoá trả phí; Imagen không còn được nhắc
  ở đó — Imagen qua đường này chưa có trong tài liệu, chưa đo.
- **Nhóm model** trong `GET /v1/models` (Provider: «Trò chuyện» / «Tạo ảnh» / «Video»; Studio «Vẽ ảnh» chỉ liệt kê
  model ảnh, «Trò chuyện» không lẫn chúng) — suy từ danh sách API gốc trong `_kha_nang` của `module.py`:
  - `"kha_nang": ["video"]`: có `predictLongRunning` (Veo).
  - `"kha_nang": ["anh"]`: có `predict` (Imagen); **hoặc** có `generateContent` và id có một **đoạn** (tách theo `-` `_`
    `.` `/`) đúng bằng `image` (`gemini-3.1-flash-image`, `gemini-3-pro-image-preview`…; `imagery` không tính),
    **hoặc** hai đoạn liền nhau `nano` `banana` (`gemini-nano-banana-2.1`, `nano-banana-pro-preview` — tên thương
    hiệu Google đặt cho dòng model ảnh; trang tạo ảnh ghi mọi model ảnh có hậu tố `image` TRỪ
    `gemini-nano-banana-2.1`). Model ảnh dạng Gemini có `supportedGenerationMethods` giống hệt model chat và Google
    không có trường nào nêu kiểu đầu ra, nên đây là tên Google đặt — hai chỗ **duy nhất** module phân loại theo chữ.
    Đo trên 46 id thật (12/10/2026): đúng 8 model ảnh dạng Gemini, không dương tính giả. Không đọc
    `displayName`/`description`: mô tả model chat cũng nhắc «image» như đầu vào.
  - Giới hạn đã biết: model ảnh mới mang tên khác cả hai quy ước sẽ **không** vào nhóm «Tạo ảnh» (web2api vẫn chuyển
    yêu cầu theo tên). Danh sách gốc lỗi thì `/v1/models` vẫn trả danh sách chat, không nhãn.
- **Video**: `POST /v1/videos` (thân JSON) → API gốc Veo `{gốc}/v1beta/models/<model>:predictLongRunning`;
  `GET /v1/videos/{id}` hỏi thao tác tới `completed`; `GET /v1/videos/{id}/content` tải tệp theo stream. Không
  dùng `/v1beta/openai/videos` vì trang tương thích của Google không ghi cách tải tệp. Gemini Omni (Interactions
  API) chưa hỗ trợ.
- Khoá chỉ đi bằng header (`Authorization: Bearer` hoặc `x-goog-api-key`), không vào URL hay nhật ký, và chỉ gửi
  tới máy của `base_url`; chuyển hướng tải video sang máy khác thì bỏ khoá.
- **Khoá bị từ chối** (401/403 → 24 giờ; 429 → theo `Retry-After` nếu khoá từng làm được việc đó, chưa từng thì
  24 giờ; 400 → chỉ khi một khoá khác làm được cùng yêu cầu) được nhớ theo **(khoá, việc)**: khoá miễn phí bị từ chối tạo ảnh vẫn chat bình thường. Mọi khoá bị từ
  chối → `403 no_key_permitted` nói rõ cần khoá trả phí. Sức khoẻ báo `kha_nang` từng khoá: `chua_ro` /
  `dung_duoc` / `bi_tu_choi` — không thử bằng lời gọi tốn tiền.

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini/gemini-3.1-flash-lite-image", "prompt": "Con mèo đội nón lá", "response_format": "b64_json"}' \
  $W/v1/images/generations
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini/veo-3.1-lite-generate-preview", "prompt": "Sóng biển lúc bình minh", "seconds": 4}' \
  $W/v1/videos                                   # → {"id": "gemini:…", "status": "queued", ...}
```

**Chưa đo với Google thật**: dạng lỗi thật cho khoá miễn phí (giả định 400/403/429), luồng Veo, model nào còn
sống, ảnh/video qua proxy Cloudflare (Worker `deploy/cloudflare-gemini-proxy.js` đi chuyển hướng bằng
`redirect: "follow"` nên có thể mang khoá sang máy đích khi tải video — chưa đo).

## Giới hạn đã biết

- **Chưa có** nhúng (embeddings), video bằng Gemini Omni. Tạo ảnh/video trên web: Studio, chế độ «Ảnh»/«Video».
- `POST /v1/videos` chỉ nhận JSON: SDK `openai` gửi `videos.create` bằng `multipart/form-data` → `415`.
- Khoá bị thu hồi **sau** khi cài mà Google trả `400` (không phải 401/403) thì module không tự đổi khoá,
  vì `400` được coi là lỗi của yêu cầu. Bớt khoá đó bằng dạng `{"giu": [...]}` (xem «Cấu hình»).
- Proxy Cloudflare có đặt `PROXY_TOKEN` chưa dùng được (xem trên).
- Chưa đo RAM và đĩa thật của module: `ram_them_mb` và `dia_mb` trong `manifest.json` để `null`.
- Chưa kiểm với dịch vụ thật: `base_url` qua proxy Cloudflare, khoá bị thu hồi sau khi cài, hết hạn mức
  (`429`) thật của Google, toàn bộ phần tạo ảnh và video.
- Module chạy **trong tiến trình lõi**, có quyền như lõi: chỉ cài từ kho git tin cậy.
