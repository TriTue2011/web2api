# Module gemini-api — Gemini API (Google AI Studio)

Module này gọi các model Gemini của Google bằng khoá API của Google AI Studio. Nó chuyển tiếp **thẳng**
tới endpoint tương thích OpenAI chính thức của Google
(`https://generativelanguage.googleapis.com/v1beta/openai`) và giữ nguyên mọi trường của yêu cầu, nên
chữ ký suy nghĩ của Gemini 3 (trường `extra_content` của lệnh gọi công cụ) đi theo mà không cần dịch.
Phần module thêm vào: xoay vòng nhiều khoá, cho khoá nghỉ khi lỗi, đổi địa chỉ gốc để vượt chặn vùng,
chuẩn hoá lỗi, danh sách model và kiểm sức khoẻ. Tiền tố model: `gemini`; phiên bản `0.1.0`.

Tài liệu đầy đủ (cài từng bước, ảnh giao diện, bảng đã kiểm với Google thật) nằm ở mục «Module
gemini-api» trong `README.md` ở gốc kho. File này là bản tóm tắt đặt ngay cạnh mã.

## Cài

Module nằm trong kho git của dự án nên cài lúc chạy, không dựng lại ảnh. Với web2api đã chạy
(`W` là địa chỉ web2api, `K` là khoá quản trị):

```bash
curl -s -H "Authorization: Bearer $K" $W/api/modules/thong-bao          # phải thấy "id": "gemini-api"
curl -s -X POST -H "Authorization: Bearer $K" $W/api/nguon-git/cai/gemini-api
```

Hoặc bấm «Cài» ở khối thông báo git trên trang Module của web2api. Cài **không cần khoá API**: xong bước
này module đã nằm trên máy nhưng chưa liên kết — chưa có model trong `/v1/models`, gọi chat trả `404`
«chưa có khoá» — cho tới khi thêm khoá (dưới đây). Module cần gói Python `httpx`
(khai ở `yeu_cau_goi` trong `manifest.json`); ảnh lõi đã có sẵn gói này.

## Cấu hình

Nhập ở thẻ Gemini trên trang Module, hoặc bằng `POST /api/modules/gemini-api/cai`:

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `api_keys` | Có | Một hoặc nhiều khoá lấy ở https://aistudio.google.com/apikey (mỗi dòng một khoá; hoặc một chuỗi cách nhau bằng dấu phẩy). Lưu riêng ở `<thư mục dữ liệu>/bi_mat/gemini-api.json` quyền 0600, không API nào trả lại khoá. |
| `base_url` | Không | Mặc định gọi thẳng Google. Đặt địa chỉ proxy khi bị chặn vùng; thiếu `/v1beta/openai` thì module tự thêm. Phải bắt đầu bằng `http://` hoặc `https://` và có tên máy. |

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

Ở một số nước (có thể gồm Việt Nam) Google chặn địa chỉ IP với lỗi chứa «User location is not supported».
Module nhận ra lỗi này (mã 400 hoặc 403 kèm chữ «location») và gợi ý đặt `base_url` qua proxy; lỗi chặn
vùng **không** làm khoá bị đánh dấu hỏng vì mọi khoá đều sẽ bị như nhau.

Cách vượt: dựng một Cloudflare Worker từ `deploy/cloudflare-gemini-proxy.js` ở gốc kho (Worker chuyển
nguyên đường dẫn sang `generativelanguage.googleapis.com`), rồi cài lại với
`"base_url": "https://<tên>.workers.dev"`. Để trống biến `PROXY_TOKEN` của Worker: web2api chưa gửi được
header `X-Proxy-Token`.

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

## Giới hạn đã biết

- Chỉ có chat và danh sách model. **Chưa có** nhúng (embeddings), tạo ảnh, video của Gemini.
- Khoá bị thu hồi **sau** khi cài mà Google trả `400` (không phải 401/403) thì module không tự đổi khoá,
  vì `400` được coi là lỗi của yêu cầu. Bớt khoá đó bằng dạng `{"giu": [...]}` (xem «Cấu hình»).
- Proxy Cloudflare có đặt `PROXY_TOKEN` chưa dùng được (xem trên).
- Chưa đo RAM và đĩa thật của module: `ram_them_mb` và `dia_mb` trong `manifest.json` để `null`.
- Chưa kiểm với dịch vụ thật: `base_url` qua proxy Cloudflare, khoá bị thu hồi sau khi cài, hết hạn mức
  (`429`) thật của Google.
- Module chạy **trong tiến trình lõi**, có quyền như lõi: chỉ cài từ kho git tin cậy.
