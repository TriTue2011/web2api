# Thêm nhà cung cấp AI bằng preset (không sửa mã)

**Bước 1 — Mở ô «Thêm nhà cung cấp».** Vào **Hệ thống → tab «Từ xa»**,
mục «Thêm nhà cung cấp» hiện trên cùng form đăng ký module từ xa.

**Bước 2 — Chọn preset.** Chọn một trong 5 preset (Tương thích OpenAI,
Agnes AI chat, NVIDIA NIM, TokenRouter, 9Router) hoặc «Tuỳ chỉnh tương
thích OpenAI» để tự nhập địa chỉ. Preset điền sẵn mã, tên, tiền tố model
và địa chỉ gốc — sửa lại được nếu cần. Ghi chú giới hạn của từng nhà hiện
ngay dưới ô chọn; cách lấy khoá hiện dưới ô nhập khoá.

**Bước 3 — Điền khoá, bấm «Kiểm tra».** Dán khoá API (ô khoá tự xoá sau
mỗi lần kiểm tra/đăng ký, không hiện lại). Nút «Kiểm tra» chỉ gọi
`GET /models` rất ngắn, không tốn token, và nói rõ sai ở đâu:
«khoá» (401/403), «hạn mức» (429), «địa chỉ» (không kết nối/SSRF),
«mô hình» (không có model đã nhập). Khoá không bao giờ lọt vào lỗi.

**Bước 4 — Đăng ký và dùng.** Bấm «Kiểm tra và đăng ký»: nhà mới hiện
trong danh sách module và trong combo model (dạng `tien_to/model`),
dùng được ngay trong chat. Với 9Router: địa chỉ mặc định `localhost`
bị chặn chống SSRF — đổi thành IP LAN của máy chạy 9Router.

## Cách đọc lỗi «Kiểm tra»

| Thông báo | Nghĩa | Làm gì |
|---|---|---|
| Khoá API sai hoặc hết hạn | 401/403 | Lấy lại khoá ở trang chính thức |
| Hết hạn mức gọi | 429 | Chờ rồi thử lại |
| Không kết nối được tới địa chỉ | Mạng/SSRF/timeout | Kiểm tra địa chỉ, mạng, IP LAN |
| Địa chỉ không có đường /models | 404 | Base URL thường kết thúc bằng `/v1` |
| Không thấy model … | Model sai tên | Bấm «Kiểm tra» không nhập model để xem số model có |

Lưu ý: Agnes preset này **chỉ chat** — sinh ảnh/video cần module riêng.
