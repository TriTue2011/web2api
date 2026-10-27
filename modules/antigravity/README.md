# Module antigravity — Antigravity (Google AGY CLI)

Module này kết nối tới tiến trình **cầu nối Antigravity** (`dich-vu/antigravity-cau-noi/`) chạy trên máy chủ để sử dụng ứng dụng chính thức `agy` (Antigravity CLI) ở chế độ không tương tác. Không gọi API Google trực tiếp, không giả danh ứng dụng, không đọc/in token. Tiền tố model: `antigravity`; phiên bản `0.1.0`.

Tài liệu chi tiết xem tại `docs/THAM-CHIEU.md` ở gốc kho (mục 4) và `dich-vu/antigravity-cau-noi/README.md`.

## Cần gì để chạy

- Tiến trình cầu nối `dich-vu/antigravity-cau-noi/cau_noi.py` đang chạy trên máy chủ (mặc định nghe `127.0.0.1:8300`). web2api chạy trong Docker thì `127.0.0.1` là chính container: cho cầu nối nghe ở IP cổng nối Docker của máy chủ và điền `http://<IP cổng nối>:8300`.
- Biến `AGY_CAU_NOI_KHOA` được đặt trên máy chủ cầu nối.
- Thư mục HOME của các tài khoản đã đăng nhập `agy` và cấu hình `toolPermission: "proceed-in-sandbox"`.

## Cài đặt

Cài lúc chạy qua git hoặc giao diện web2api:

```bash
curl -s -H "Authorization: Bearer $K" $W/api/modules/thong-bao
curl -s -X POST -H "Authorization: Bearer $K" $W/api/nguon-git/cai/antigravity
```

Cài **không cần khoá**: module sẽ ở trạng thái đã cài nhưng chưa liên kết (`da_cau_hinh: false`) cho đến khi nhập cấu hình.

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `dia_chi_cau_noi` | Có | Địa chỉ URL của cầu nối trên máy chủ (mặc định `http://127.0.0.1:8300`). |
| `khoa_cau_noi` | Có | Khoá bí mật khớp với `AGY_CAU_NOI_KHOA`. Lưu tại `<dữ liệu>/bi_mat/antigravity.json` quyền 0600. |

Lệnh cài cấu hình:

```bash
curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"cau_hinh": {"dia_chi_cau_noi": "http://127.0.0.1:8300", "khoa_cau_noi": "khoa-bi-mat"}}' \
  $W/api/modules/antigravity/cai
```

## Ví dụ sử dụng

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "antigravity/gemini-3.8-flash-high", "messages": [{"role": "user", "content": "Xin chào"}]}' \
  $W/v1/chat/completions
```

## Giới hạn & Tính năng tự động

- Tự động làm mới danh mục model qua vòng lặp định kỳ và nút «Làm mới danh sách» (NV102/NV108) mà không cần cấu hình lại.
- Báo phiên bản CLI `agy` hiện tại tại mục sức khoẻ/trạng thái.
- Chỉ hỗ trợ chat văn bản (ảnh và tập tin bị bỏ qua).
- Tốn hạn mức lớn (khoảng 12.500 token đầu vào tối thiểu mỗi lượt), thích hợp làm nguồn AI dự phòng.

## Đăng nhập tài khoản (NV271)

Tài khoản Google loại gmail có đích dùng «antigravity» đăng nhập bằng `POST /api/tai-khoan/da-luu/{id}/dang-nhap` với `module_id` = `antigravity`. Đăng nhập `agy` là bước **tương tác của người vận hành** (CLI không có lệnh đăng nhập; web2api không lái phiên tương tác, không đọc token, không gọi API Google trực tiếp). Module chỉ hỏi cầu nối «HOME mang email này còn phiên dùng được không»: có thì báo thành công; chưa có thì báo **cần người** kèm hướng dẫn (đăng nhập `agy` tương tác với đúng HOME, ghi email vào `$HOME/.agy-cau-noi-email`, bấm «Kiểm tra lại»). Việc tự động lấy mã/đường dẫn xác nhận **chưa làm được** vì lý do trên.
