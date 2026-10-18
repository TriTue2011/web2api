# Module zalo-bot — Kênh gửi ra Zalo Bot (chỉ gửi văn bản)

Gửi tin nhắn văn bản thuần ra Zalo qua Bot API (`POST /bot<TOKEN>/sendMessage`
tới `https://bot-api.zaloplatforms.com`), dùng cho sổ thiết bị (báo máy lạ) và
mọi chỗ gửi thông báo qua chuẩn `kenh_ra` của lõi. Chỉ gửi ra, không nhận tin vào.

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

## Chưa kiểm

Chưa kiểm với Zalo thật (cần token bot thật + bot đã có người nhận). Test hiện
tại dùng máy chủ Zalo giả cục bộ.
