# gemini-web — Module web2api cho Gemini bản web (gemini.google.com)

Trò chuyện với Gemini bản web qua **giao thức nội bộ** của web app
(`batchexecute`), xác thực bằng cookie Google: `__Secure-1PSID` (+ `__Secure-1PSIDTS`
tùy chọn). **Miễn phí, không cần API key, không cần browser.**

Tiền tố model: `gemini-web`. Model: `gemini-web/3.5-flash` (mặc định),
`gemini-web/3.1-pro`, `gemini-web/3.5-flash-lite`. Tên `auto` (hoặc bỏ trống)
dùng model mặc định; tên lạ bị chỉ đích danh → `400`.

## Khả năng

| Khả năng | Phương thức | Ghi chú |
|---|---|---|
| Trò chuyện | `chat` | Stream (SSE) + không stream |
| Danh sách model | `models` | 3 model trên |
| Gọi công cụ (tools) | `chat` | Bóc tách tool_calls qua protocol mô phỏng; stream gom đủ mới phát |
| Đầu ra có cấu trúc | `chat` | `response_format` json_object & json_schema (kiểm tra bằng schema, retry 1 lần; pattern chưa hỗ trợ) |

Ảnh/video **chưa làm** ở giai đoạn này (phần ảnh trong messages bị bỏ qua).

## Cần gì

- Một hoặc nhiều bộ cookie từ tab gemini.google.com đã đăng nhập
  (DevTools F12 → Application → Cookies), dạng `k1=v1; k2=v2`.
  Cần ít nhất `__Secure-1PSID`; `__Secure-1PSIDTS` giúp phiên bền hơn.
- Mạng ra tới `gemini.google.com` (module tự cài đặt giao thức bằng `httpx`,
  không cần gói ngoài).

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `cookies` | Có | Một hoặc nhiều bộ cookie Google, dạng `k1=v1; k2=v2`. Nhiều bộ thì xoay vòng khi lỗi. |
| `co_so_url` | Không | **Chỉ dùng cho kiểm thử** với máy chủ giả. Để trống dùng `https://gemini.google.com`. |

**Lấy cookie:** mở tab gemini.google.com đã đăng nhập → DevTools (F12) →
Application → Cookies → copy `__Secure-1PSID` (và `__Secure-1PSIDTS` nếu có)
ghép thành chuỗi `__Secure-1PSID=...; __Secure-1PSIDTS=...`.

Manifest khai `"nhan_cookie": {"mien": "gemini.google.com"}` trên trường
`cookies`: chỉ trường này nhận cookie, và chỉ cookie của `gemini.google.com`.

Lúc **Cài đặt**, module kiểm từng bộ cookie bằng `init()` (GET trang chủ lấy mã
phiên `SNlM0e`) — rẻ, không tốn lượt chat. Bộ nào bị 401/403 thì đánh dấu hỏng,
bộ lỗi tạm thời vẫn được thử lại ở lượt sau.

## Ví dụ dùng

Trò chuyện thông thường:

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini-web/3.5-flash", "messages": [{"role": "user", "content": "Xin chào"}]}' \
  $W/v1/chat/completions
```

Stream:

```bash
curl -sN -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini-web/3.1-pro", "messages": [{"role": "user", "content": "Kể chuyện cười"}], "stream": true}' \
  $W/v1/chat/completions
```

Gọi công cụ (Tool Calls):

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-web/3.5-flash",
    "messages": [{"role": "user", "content": "Thời tiết ở Tokyo?"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Lấy thời tiết hiện tại",
        "parameters": {
          "type": "object",
          "properties": {"location": {"type": "string"}},
          "required": ["location"]
        }
      }
    }]
  }' \
  $W/v1/chat/completions
```

Đầu ra có cấu trúc (JSON Schema):

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-web/3.5-flash",
    "messages": [{"role": "user", "content": "Thông tin thủ đô nước Pháp"}],
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "capital_info",
        "schema": {
          "type": "object",
          "properties": {
            "city": {"type": "string"},
            "country": {"type": "string"}
          },
          "required": ["city", "country"]
        }
      }
    }
  }' \
  $W/v1/chat/completions
```

## Lưu ý kỹ thuật (NV134)

- **Gọi công cụ & Stream:** Khi yêu cầu có `tools`, module chèn giao thức văn bản nghiêm ngặt vào prompt và bóc tách đầu ra. Khi stream SSE, bộ lọc `LocToolCallStream` chặn không phát nửa lời gọi hoặc cú pháp thô ra luồng văn bản; gom đủ toàn bộ lời gọi rồi phát qua chunk `tool_calls` và `finish_reason: "tool_calls"`.
- **Bảo vệ chống prompt injection:** Chỉ những công cụ nằm trong danh sách `tools` của yêu cầu mới được chấp nhận; công cụ lạ do model tự ý sinh ra sẽ bị loại bỏ hoàn toàn. Tham số được kiểm tra theo JSON Schema; sai kiểu hoặc thiếu thẻ đóng sẽ bị loại bỏ (không đoán sửa).
- **Đầu ra có cấu trúc (best-effort):** Giao diện web Gemini không có constrained decoding (giải mã ràng buộc ở tầng suy luận). Module tiếp cận «cố gắng hết sức»: hướng dẫn nghiêm ngặt qua prompt, bóc tách JSON và kiểm tra lại bằng schema (hỗ trợ type, properties, required, items, enum, min/max...; từ khoá `pattern` chưa hỗ trợ, bị từ chối có chủ đích để ngăn ngừa hoàn toàn nguy cơ ReDoS). Nếu sai, thử lại tối đa 1 lần; nếu vẫn sai trả 502 rõ ràng (`structured_output_failed`).
- **Giới hạn:** Phiên hội thoại (nối nhiều lượt vào cùng một cuộc trò chuyện web): làm sau.
- **Tham số bỏ qua:** `temperature`, `top_p`, `max_tokens`, `parallel_tool_calls`, `safety` được chấp nhận và không báo lỗi, nhưng «không có tác dụng» trên giao diện web. Giới hạn prompt tối đa 120,000 ký tự (vượt quá trả 413 rõ ràng).

## Cách giao tiếp (học từ kho tham khảo)

Kho tham khảo `chatgpt2api` (`api/gemini_web.py`) dùng gói `gemini_webapi`:
`GeminiClient(secure_1psid=…, secure_1psidts=…)` → `init()` (tự xoay
`__Secure-1PSIDTS` ~600s) → `generate_content_stream()` cho stream
(`text_delta` từng phần) hoặc gom lại cho không stream; `AuthError` của gói
→ 401; registry model động + bí danh (`3.5-flash` → `gemini-3-flash`…);
pool nhiều tài khoản xoay vòng, tài khoản đã xác thực được xếp trước.

Module này cài đặt lại luồng đó bằng `httpx` (không phụ thuộc gói ngoài):
cookie → GET `/` lấy `SNlM0e` → POST `/_/BardChatUi/data/batchexecute`
(`f.req` + `at`) → bóc khung `["wrb.fr", …]` lấy văn bản; stream tính delta
giữa các khung.

## Sự cố thường gặp

- `401` «Mọi tài khoản gemini-web đều bị từ chối»: cookie hết hạn — đăng nhập
  lại gemini.google.com trên trình duyệt rồi export lại cookie.
- `429` «bị giới hạn lượt»: đợi một lúc rồi thử lại; có nhiều bộ cookie thì
  module tự chuyển sang bộ kế.
- `502`/`504`: phía Google lỗi hoặc chậm — thử lại sau. Thông báo chỉ nêu mã
  và kiểu lỗi; nội dung đáp án thô của Google và cookie **không bao giờ**
  xuất hiện trong phản hồi hay nhật ký.
- Giao thức web của Google không chính thức và có thể đổi: module **chưa kiểm
  với dịch vụ thật** (chỉ kiểm với máy chủ giả trong `test/`). Google đổi web
  có thể làm module ngừng hoạt động — dùng cá nhân, tự chịu rủi ro.
