# Module zalo-bot — Kênh Zalo Bot hai chiều

Gửi tin nhắn văn bản thuần ra Zalo qua Bot API (`POST /bot<TOKEN>/sendMessage`
tới `https://bot-api.zaloplatforms.com`), dùng cho sổ thiết bị (báo máy lạ) và
mọi chỗ gửi thông báo qua chuẩn `kenh_ra` của lõi. **Nhận tin vào qua webhook
của lõi** (Zalo Bot không có getUpdates dùng được khi webhook đang bật): module
chỉ phiên dịch payload Zalo → tin chuẩn (chuẩn `tin_vao`, CHUAN-MODULE mục
12.3b), để lõi ra lệnh trong chat và phân quyền theo người gửi.

## Bài học thiết kế (đo thật từ mã c2a)

Nhà chạy BỐN bot Zalo. Khi không nêu bot, tin đi ra bot ĐẦU danh sách → báo nhầm
chỗ mà API vẫn trả `ok: true`; cùng `chat_id` gọi bằng token bot khác trả
`410 "The chat_id is invaild"`. Vì vậy module bắt buộc **mỗi đích khai rõ bot**
(`id đích → bot → chat_id`), KHÔNG có «bot mặc định», token bot được chọn THEO
ĐÍCH ở đúng lời gọi mạng (không biến/ngữ cảnh toàn cục). Phản hồi 410/chat sai
→ thất bại rõ ràng (`chat_sai`), không thử bot khác.

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `bot_token` | Có | Token từng bot Zalo (mỗi dòng một token, `danh_sach_bi_mat`). Thứ tự trong danh sách xác định bot; đặt tên ở «Tên bot». Lưu riêng, không API nào trả lại. |
| `ten_bot` | Không | Tên các bot, cùng thứ tự với «Token các bot Zalo», ngăn cách bằng dấu phẩy hoặc xuống dòng (ví dụ `bot-nha, bot-cong-ty`). Thiếu tên thì tự đặt `bot-1`, `bot-2`… |
| `danh_sach_dich` | Không | Chuỗi JSON: `[{"id": "nha", "ten": "Nhà", "chat_id": "...", "bot": "bot-nha"}]`. Mỗi đích BẮT BUỘC nêu bot (tên bot hoặc số thứ tự 0, 1, 2…) — sai bot thì tin đi nhầm chỗ mà Zalo vẫn báo ok. |

## Hành vi

- Tin dài: tối đa 1990 ký tự/tin, tách ở ranh giới dòng (dòng đơn dài hơn thì cắt
  cứng), tối đa 5 tin, phần dư cắt kèm «…» ở tin cuối.
- Chỉ văn bản THUẦN: thân POST đúng `{"chat_id", "text"}` (không `parse_mode`/
  markdown); văn bản gửi nguyên văn.
- Kết quả: `{"ok": True}` chỉ khi Zalo trả `ok` là bool `True` thật. Lỗi → mã ngắn:
  `chua_cau_hinh`, `dich_sai`, `noi_dung_sai`, `chat_sai` (410),
  `yeu_cau_sai` (4xx khác), `het_han_muc` (429, báo thất bại ngay, không ngủ),
  `may_chu_loi` (5xx), `phan_hoi_sai` (không JSON / `ok` không phải bool),
  `mang_loi`, `loi_khong_ngu`. Không lộ `description` thô. Không retry (lõi NV107
  đã thử lại theo chu kỳ của nó).
- Bảo mật: token nằm trong URL → chặn log URL (đặt logger `httpx`/`httpcore` về
  WARNING trong lúc gọi); không log URL/token/chat_id (chỉ log id đích + tên kiểu
  ngoại lệ). `kenh_ra()` chỉ trả `[{id, ten}]`.

## Nhận tin vào qua webhook (lõi làm cửa, module chỉ phiên dịch)

Zalo Bot không có getUpdates dùng được khi webhook đang bật, nên tin vào đi qua
cửa webhook chung của lõi (`POST /api/webhook/zalo-bot/<bí-mật>`): lõi xác thực
bí mật đường dẫn (so sánh thời gian hằng), chống phát lại theo `message_id` và
hạn dòng, rồi đưa THÂN JSON cho `webhook()` của module. Module trả
`{"ok": True, "tin_vao": [...]}`; lõi chuyển từng tin vào `kenh_vao.nhan_tin`
và BỎ khoá `tin_vao` khỏi phản hồi (nội dung tin không phản chiếu ra ngoài).

Quy tắc phiên dịch (không bịa, theo cấu trúc payload `result.message`):

- Nhiều bot CHUNG một URL webhook: payload không nói bot nào → module BẮT BUỘC
  ánh xạ `chat.id` → đích đã khai trong «Danh sách đích» → bot của đích đó.
  Tin từ chat không khớp đích nào bị BỎ (đếm `bo_qua_chat_la` trong `suc_khoe`,
  không trả lời nơi lạ).
- Chỉ sự kiện CÓ `text` là chuỗi mới thành tin (ảnh/sticker/sự kiện khác tự rớt).
- `nguoi_gui` = `from.id` (KHÔNG phải `chat.id` — trong nhóm hai id khác nhau);
  `noi_gui` = id đích đã khớp (để lõi trả lời đúng chỗ); `la_nhom` khi
  `chat_type` khác `private`; `id_tin` ghép tên bot + `message_id` (hai bot chung
  webhook không đụng id).
- Mọi chuỗi ngoài là dữ liệu KHÔNG TIN: cắt độ dài (văn bản ≤ 4000, tên ≤ 60),
  bỏ ký tự điều khiển; không đưa nội dung vào log; token không vào log/`suc_khoe`.

Từng bước dựng (trên bot.zapps.me — Zalo Bot Platform):

1. Tạo bot: đăng nhập bot.zapps.me → tạo ứng dụng/bot, lấy **token** của từng
   bot; bật webhook cho bot và dán URL lõi hiển thị ở trang Module
   (`https://<máy-chủ-của-bạn>/api/webhook/zalo-bot/<bí-mật>`).
2. Lấy `chat_id`: nhắn tin cho bot (chat riêng) hoặc thêm bot vào nhóm rồi nhắn;
   xem log/đường kiểm của lõi hoặc hỏi bot bằng lệnh kiểm để biết `chat_id`
   (chat riêng) và id người gửi (`from.id` — dùng để cấp quyền ở
   `/api/kenh-vao/nguoi`).
3. Đặt webhook: dán URL ở bước 1 vào cấu hình webhook của bot trên
   bot.zapps.me; lưu ý URL chứa BÍ MẬT — ai có URL là gửi được tin giả vào
   (lõi vẫn xác thực bí mật + chống phát lại, nhưng đừng để lộ URL).
4. Cấp quyền: vào Hệ thống → Kênh vào → Người, cấp vai (`quan_tri`/`nguoi_dung`)
   cho từng `from.id`; người chưa cấp quyền là người lạ — không được thực thi
   lệnh, không nhận phản hồi.

Bảo mật thêm: KHÔNG tự gọi `setWebhook`/`deleteWebhook` (người dùng đặt tay);
`bi_mat_webhook` lưu ở kho bí mật (0600), chưa đặt thì cửa webhook trả 503.
Xác thực: tài liệu chính thức quy định mọi request webhook kèm header
`X-Bot-Api-Secret-Token` mang `secret_token` — header KHÔNG tới module (lõi
chỉ đưa thân JSON), nên module dựa vào bí mật trong đường dẫn của cửa webhook
lõi (so sánh thời gian hằng) + chống phát lại theo `message_id`.

## Nguồn sự thật (đọc từ tài liệu chính thức)

bot.zapps.me/docs, trang webhook — đọc ngày 19/10/2026: thân là JSON object
`{"ok": true, "result": {...}}`; `event_name` nhận `message.text.received` /
`message.image.received` / `message.sticker.received` / `message.voice.received`
/ `message.unsupported.received`; `chat_type` là `PRIVATE`/`GROUP` (IN HOA);
`date` là epoch MILI giây (mẫu `1750316131602`).

## Chưa kiểm

Chưa kiểm với Zalo thật (cần token bot thật + bot đã có người nhận). Test hiện
tại dùng Zalo giả cục bộ (payload theo tài liệu chính thức nói trên). Giá trị
`chat_type` ngoài `PRIVATE`/`GROUP` và header `X-Bot-Api-Secret-Token` (không
tới module) chưa kiểm với Zalo thật.
