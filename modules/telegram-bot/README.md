# Module telegram-bot — Telegram (kênh gửi ra)

Gửi thông báo văn bản ra Telegram qua **Bot API công khai** của Telegram, theo chuẩn
`kenh_ra` của lõi (docs/CHUAN-MODULE.md mục 8): sổ thiết bị (NV107) và mọi chỗ khác
gửi qua `POST /api/gui/telegram-bot`.

**Chỉ GỬI RA văn bản.** Không nhận tin vào, không webhook, không bot trả lời —
kênh chat hai chiều không thuộc lõi (đã quyết định ở NV113).

## Cách hoạt động

- Một `bot_token` (lấy từ @BotFather). Địa chỉ gọi **cố định**
  `https://api.telegram.org` trong mã — không cho cấu hình URL tuỳ ý (chống SSRF).
- Danh sách đích do chủ máy khai (`dich`, dạng JSON): mỗi đích có `id` (do chủ máy
  đặt, duy nhất), `ten` hiển thị, `chat_id`, `chu_de` (id chủ đề/topic trong nhóm,
  tuỳ chọn). Đích nào gửi tới chat nào do **đích đã khai** quyết định — không có
  «bot mặc định», không đoán.
- `POST https://api.telegram.org/bot<TOKEN>/sendMessage` với thân
  `{"chat_id", "text", "message_thread_id"?, "disable_web_page_preview": true}`.
  **Không gửi `parse_mode`** (văn bản thuần — tránh lỗi định dạng và tránh bị chèn).
- Tin dài quá 4096 ký tự: tự tách ở ranh giới dòng, tối đa 5 tin, phần dư cắt kèm «…».
- Lỗi mạng (kể cả hết giờ kết nối/đọc): thử lại tối đa 1 lần; 4xx không thử lại;
  429 (`retry_after`): báo thất bại, không ngủ dài (lõi đã có hạn dòng).
- Kết quả `{"ok": bool, "ma": "<mã ngắn>"}` — `ok` luôn là bool thật;
  `description` thô của Telegram không bao giờ lộ ra kết quả/log/phản hồi.

## Bảo mật

Token nằm TRONG URL nên module chặn rò rỉ ở ba chỗ:

1. Trong lúc gọi, logger `httpx`/`httpcore` bị nâng lên `WARNING` (httpx ghi URL ở
   mức INFO/DEBUG) rồi khôi phục.
2. Không bao giờ `str()` ngoại lệ httpx (chúng có thể in URL chứa token) — log và
   kết quả chỉ dùng tên kiểu ngoại lệ.
3. `kenh_ra()` chỉ trả `[{id, ten}]` — không token, không `chat_id` thô.

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `bot_token` | Có | Token bot từ @BotFather. Lưu riêng dạng bí mật, không trả lại. |
| `dich` | Không | Danh sách đích dạng JSON, ví dụ `[{"id": "nha", "ten": "Nhà", "chat_id": 123456789, "chu_de": 7}]`. `id` duy nhất; `chat_id` là id chat Telegram; `chu_de` là id chủ đề trong nhóm (tuỳ chọn). |

Lúc cài (`POST /api/modules/telegram-bot/cai`), module kiểm token bằng
`GET /getMe` (rẻ, không tốn gì) và thẩm định cấu trúc danh sách đích.

## Ví dụ dùng

```bash
# Gửi tới đích "nha" (cần khoá có phạm vi gui:telegram_bot)
curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"kenh_id": "nha", "noi_dung": {"van_ban": "Máy lạ vừa vào mạng nhà"}}' \
  $W/api/gui/telegram-bot
# → {"ok": true, "kenh": "nha"}
```

## Giới hạn

- Chưa kiểm với api.telegram.org thật (test với máy chủ Telegram giả cục bộ).
- Chỉ văn bản; không gửi ảnh/tệp qua kênh này (lõi có thể gửi `_tep`, module hiện
  chỉ dùng `van_ban`).
