# Kiểm thử đăng nhập Google qua may-dang-nhap (NV145b)

## Cài đặt
1. Cài module `may-dang-nhap`, nhập URL dịch vụ may-dang-nhap + khoá dịch vụ.
2. Hệ thống → AI → Tài khoản đã lưu: chọn nguồn **may-dang-nhap** (hiện vì module đã
   có hợp đồng `dang_nhap`).

## Đăng nhập
3. Nhập email + mật khẩu Google (thêm khoá TOTP nếu có), bấm **Lưu và đăng nhập**.
4. Đọc trạng thái: *Đang đăng nhập…* (tự hỏi tiếp) → *Đã đăng nhập* /
   *chờ mã 2FA* / *cần bạn xử lý* / lỗi có mã.

## Mã 2FA
5. Khi hiện ô **Mã 2FA**: nhập mã 6 số rồi bấm **Gửi mã**. Nếu tài khoản đã lưu
   khoá TOTP, dịch vụ tự điền mã, không cần nhập tay.

## Khi nào là «cần người»
6. **Thử thách CAPTCHA**: hệ thống không tự giải — mở trình duyệt xử lý tay.
7. **Chạm trên điện thoại**: bấm «Có, đó là tôi» trên điện thoại.
8. **Sai mật khẩu / bị khoá / cần xác minh điện thoại**: hệ thống DỪNG HẲN để
   bảo vệ tài khoản — kiểm tra thủ công rồi lưu lại tài khoản mới thử tiếp.
9. Quá **3 lần thử/giờ/tài khoản**: đợi hết giờ (chống Google khoá).

## Chuyển phiên sang module AI
10. Sau khi đăng nhập xong: tab Trình duyệt → cookie của hồ sơ sang trường
    `cookies` của module `gemini-web` (cơ chế cookie-sang-module có sẵn).

*Chưa kiểm với Google thật — mọi luồng trên mới đo bằng dịch vụ giả.*
