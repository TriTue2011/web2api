# Đọc mã xác minh từ Gmail (chỉ đọc)

Module này giúp web2api **tự đọc mã xác minh 6 số** mà các dịch vụ gửi về
hộp thư Gmail của bạn (ví dụ khi đăng nhập bằng email + mật khẩu), để bạn
không phải mở Gmail chép mã bằng tay. Trong form thêm tài khoản, bạn tích
«Lấy mã từ Gmail» thì hệ thống tự lấy mã; không tích thì dùng cách khác
(ứng dụng xác thực 2FA).

> ⚠️ **ĐỌC KỸ TRƯỚC KHI DÙNG — RỦI RO THẬT:** để đọc được thư, Google bắt bạn
> tạo «Mật khẩu ứng dụng». Mật khẩu này cho phép **đọc TOÀN BỘ hộp thư Gmail**
> của bạn (mọi thư cũ, thư ngân hàng, thư công việc…), không chỉ thư chứa mã.
> Vì vậy **KHUYẾN NGHỊ MẠNH**: đừng dùng Gmail chính. Hãy tạo một **Gmail PHỤ
> chỉ để nhận mã** (xem bước 4 dưới đây), rồi đặt bộ lọc trong Gmail chính tự
> chuyển tiếp các thư mã xác minh sang Gmail phụ. Như vậy mật khẩu ứng dụng
> chỉ đọc được hộp thư phụ — lộ cũng không mất gì quan trọng.

## Cần chuẩn bị gì

1. Một tài khoản Gmail (khuyến nghị: Gmail **phụ** mới tạo, chỉ để nhận mã).
2. Máy chủ web2api có mạng ra Internet (module chỉ gọi `imap.gmail.com:993`).

## Bước 1 — Bật «Xác minh 2 bước» cho tài khoản Google

1. Mở trình duyệt, vào **myaccount.google.com** và đăng nhập Gmail phụ.
2. Vào mục **Bảo mật** (Security) → **Xác minh 2 bước** (2-Step Verification).
3. Bấm **Bắt đầu** / **Bật**, làm theo hướng dẫn (thường xác nhận bằng điện thoại).

## Bước 2 — Tạo «Mật khẩu ứng dụng»

1. Vẫn ở myaccount.google.com → **Bảo mật** → tìm mục **Mật khẩu ứng dụng**
   (App passwords). (Nếu không thấy, gõ «mật khẩu ứng dụng» vào ô tìm kiếm
   của trang tài khoản Google.)
2. Đặt tên gợi nhớ, ví dụ `web2api-doc-ma`.
3. Bấm **Tạo** — Google hiện một dãy **16 chữ cái** (dạng `abcd efgh ijkl mnop`).
   **Chép ngay**, đóng trang là mất, phải tạo lại.
4. Giữ dãy này cẩn thận như mật khẩu — ai có nó đọc được cả hộp thư.

## Bước 3 — Bật IMAP trong Gmail

1. Mở Gmail (mail.google.com) của tài khoản phụ.
2. Bấm **⚙️ Cài đặt** → **Xem tất cả chế độ cài đặt** → tab **Chuyển tiếp và
   POP/IMAP** (Forwarding and POP/IMAP).
3. Ở mục **Truy cập IMAP**, chọn **Bật IMAP** (Enable IMAP) → **Lưu thay đổi**.

## Bước 4 — (Khuyến nghị) Tự chuyển tiếp thư mã sang Gmail phụ

Nếu bạn vẫn muốn mã gửi về Gmail chính: trong Gmail **chính**, vào
Cài đặt → **Bộ lọc và địa chỉ bị chặn** → **Tạo bộ lọc mới**: điều kiện
«Có các từ» ghi ví dụ `mã xác minh OR verification code` (tuỳ dịch vụ bạn
dùng), bấm **Tạo bộ lọc** → tích **Chuyển tiếp tới** → chọn Gmail phụ
(cần xác nhận một lần). Từ nay thư mã tự chạy sang hộp thư phụ.

## Bước 5 — Khai báo trong web2api

Vào tab **Hệ thống → Module**, tìm module **«Đọc mã xác minh từ Gmail (chỉ đọc)»**,
bấm **Cài đặt** / **Cấu hình**:

- **Địa chỉ Gmail:** địa chỉ Gmail phụ, ví dụ `maphu@example.com`.
  (Nhiều hộp thư: mỗi dòng một địa chỉ, cùng thứ tự với mật khẩu bên dưới.)
- **Mật khẩu ứng dụng:** dãy 16 chữ cái vừa tạo (có thể dán kèm khoảng trắng,
  hệ thống tự bỏ).

Bấm **Lưu** — hệ thống đăng nhập thử một lần để kiểm tra (chỉ LOGIN rồi
LOGOUT ngay, **không đọc thư nào**). Sai mật khẩu sẽ báo rõ, không lưu.

## Module này KHÔNG làm gì (cam kết)

- **Chỉ đọc:** mở hộp thư ở chế độ chỉ-đọc, không bao giờ đánh dấu đã đọc,
  xóa, di chuyển hay sửa thư nào.
- **Chỉ lấy mã:** chỉ xét thư đến **sau** lúc bạn bấm «lấy mã» và **từ đúng
  dịch vụ** bạn đang đăng nhập; chỉ trả về **dãy số mã**, không trả nội dung
  thư, tiêu đề hay địa chỉ người gửi cho bất kỳ ai.
- **Không ghi nhật ký:** mật khẩu, mã xác minh và nội dung thư không bao giờ
  vào log, nhật ký hay thông báo lỗi.
- **Không có trang web riêng:** module không mở thêm cổng hay đường HTTP nào;
  mã xác minh không thể lộ qua đường web.
- **Máy chủ cố định:** chỉ kết nối `imap.gmail.com:993` qua kết nối mã hoá có
  kiểm chứng chỉ — không thể bị lái sang máy chủ khác.

## Xử lý sự cố thường gặp

- **Báo `sai_dang_nhap`:** kiểm tra lại dãy 16 chữ cái (đúng tài khoản chưa?
  có bật «Xác minh 2 bước» chưa? Mật khẩu ứng dụng tạo cho đúng tài khoản Gmail
  phụ chưa?).
- **Báo `het_gio` (hết giờ chờ):** thư mã chưa về kịp — bấm lại; hoặc kiểm tra
  bộ lọc chuyển tiếp ở bước 4 có chạy không.
- **Muốn thu hồi quyền:** vào myaccount.google.com → Bảo mật → Mật khẩu ứng
  dụng → **Thu hồi** mục `web2api-doc-ma`. Module lập tức hết đọc được.
