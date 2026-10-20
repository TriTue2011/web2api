# Muse (Meta) — module web2api

Truy cập trợ lý Muse (muse.ai / Meta AI VM) qua giao thức kết nối WebSocket mã hoá Noise XX (`Noise_XX_25519_AESGCM_SHA256`).

> [!WARNING]
> **Thử nghiệm — chưa kiểm với dịch vụ thật.** Hiện tại cơ chế đăng ký thiết bị bằng mã OTP từ Meta chưa được mở tự động; module hoạt động qua máy chủ giả lập trong kiểm thử hoặc khi người dùng có sẵn khoá thiết bị tĩnh.

## Khả năng

| Khả năng | Phương thức | Ghi chú |
|---|---|---|
| Trò chuyện | `chat` | Stream qua WebSocket Noise XX bọc khung Protobuf HTTP |

## Kiến trúc giao thức

Giao diện web của Muse không sử dụng REST API truyền thống mà kết nối trực tiếp tới máy ảo qua `wss://<vm>.metaaivm.com/v1/noise`:
1. Bắt tay 3 bước Noise XX (`-> e`, `<- e, ee, s, es`, `-> s, se`) bằng Curve25519 và AES-GCM.
2. Tầng khung phân mảnh `NoiseTransportFrame` (Protobuf viết tay, tối đa 65.489 bytes/mảnh).
3. Đóng gói HTTP qua `ServiceFrame` và `ServiceRequest` gửi tới dịch vụ `daemon`.
4. Nhận phản hồi và các mảnh dữ liệu luồng `BodyChunk` chuyển đổi thành SSE chuẩn OpenAI.

## Cài đặt

Module này được cài qua giao diện **Hệ thống → Module** hoặc `/api/modules` (xem README chính).

## Cấu hình

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `device_key` | bí mật | ✅ | Khoá khôi phục thiết bị 32 bytes (hex hoặc base32/base64) |
| `delegation_token` | bí mật | ❌ | Token uỷ quyền đã ký đi kèm thông điệp Message 3 |
| `ws_url` | chuỗi | ❌ | Địa chỉ WebSocket máy ảo (mặc định dùng cổng Meta AI VM) |

**Lấy `device_key` ở đâu:** khoá này gắn với một thiết bị đã đăng ký với Meta qua mã OTP.
web2api **hiện chưa đăng ký tự động được** (Meta chưa mở cơ chế này cho bên thứ ba) nên không
có nút bấm nào để lấy khoá: bạn phải **có sẵn** khoá thiết bị tĩnh (ví dụ từ một lần đăng ký
thiết bị trước đó bằng công cụ khác của chính bạn) rồi dán vào trường `device_key`. Đừng tự
sinh chuỗi ngẫu nhiên — khoá dẫn xuất phải khớp thiết bị đã đăng ký, khoá bịa sẽ làm bắt tay
Noise XX thất bại. Chưa có khoá thì module không chạy được; đó là lý do module vẫn ở trạng
thái thử nghiệm.

## Trạng thái

Thử nghiệm — chưa kiểm với dịch vụ thật.
