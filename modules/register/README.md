# Module `register` — Đăng ký tài khoản (người trong vòng lặp)

Điều phối các bước đăng ký tài khoản **của chính bạn** ở nhà cung cấp:

1. **Bắt đầu lượt** (`bat_dau`): tạo hộp thư tạm ngẫu nhiên trên Inbucket của
   bạn, sinh mật khẩu mạnh (20 ký tự, 4 nhóm ký tự), rồi **tải trang đăng ký để
   NHẬN DIỆN bước** theo cấu trúc HTML. Thông tin tài khoản hiện **MỘT LẦN**
   trong kết quả lần này — chép ngay rồi che.
2. **Nhận diện bước:** trang form sẵn sàng → chờ bạn điền trên trình duyệt từ
   xa; trang hỏi mã → đợi mã; gặp **thử thách chống bot / xác minh điện thoại /
   trang lạ** → DỪNG ở trạng thái **«can_nguoi»**, không đi tiếp.
3. **Người làm phần tay** (điền form, vượt thử thách, nhập mã từ hộp thư, nhập
   số điện thoại) trên trình duyệt từ xa (noVNC của lõi). Module **không bao
   giờ** tự giải/vượt CAPTCHA hay bước xác minh, không tự điền biểu mẫu.
4. **Lấy mã** (`doi_ma`): hỏi vòng hộp thư của chính lượt đó; trả mã để **bạn**
   tự nhập vào trình duyệt từ xa. **Hoàn tất** (`xac_nhan_hoan_tat`) khi bạn
   báo tài khoản đã xong; **huỷ** (`huy`) thì xoá bí mật của lượt khỏi kho bí
   mật của lõi.

## Hàng rào an toàn (đọc trước khi bật)

- Module **mặc định TẮT** (`bat=false`): không chạy gì, không gọi mạng nào.
- **Một lần một tài khoản**: tối đa 1 lượt đang chạy, mặc định ≤ 1 lượt/ngày
  (cấu hình được, trần cứng 5). Không có chế độ hàng loạt.
- Mật khẩu sinh và mã khôi phục là **bí mật**: lưu trong kho bí mật của lõi
  (0600, mã hoá), chỉ hiện một lần, không vào log / trạng thái / sức khoẻ.
- **Điều khoản nhà cung cấp có thể cấm đăng ký tự động.** Module này không giả
  mạo trình duyệt, không vượt thử thách chống bot — phần tay vẫn do bạn làm —
  nhưng bạn vẫn nên đọc điều khoản của nhà cung cấp và tự cân nhắc trước khi
  bật. (Bắt buộc ghi theo spec NV174.)

## Cài đặt

1. **Tự cài Inbucket** (mã nguồn mở) trên máy nhà hoặc máy chủ của bạn, cấu
   hình miền nhận thư (ví dụ `hop-thu.example`) và bản ghi MX trỏ vào máy đó.
   Đây là hộp thư **của bạn**, không phải dịch vụ thư tạm của bên thứ ba.
2. Vào Hệ thống → Module → `register` → Cài đặt, điền:
   - «Bật luồng đăng ký» = `true`;
   - «URL trang đăng ký»: URL trang đăng ký công khai (chép sau chuyển hướng);
   - «Địa chỉ API hộp thư»: địa chỉ Inbucket của bạn;
   - «Miền hộp thư»: miền SMTP_DOMAIN khi bạn cài Inbucket;
   - «Khoá API hộp thư»: để trống nếu Inbucket không đặt sau cổng khoá.
3. Lưu. Quản trị bắt đầu lượt bằng hành động `bat_dau` trên thực thể
   `dang_ky.luot` (cần xác nhận — thực thể nguy hiểm).

## Hạn chế / chưa kiểm

- Chưa kiểm với nhà cung cấp thật và noVNC thật — test bằng trang đăng ký GIẢ
  và hộp thư GIẢ trên máy này.
- Mọi hành động qua cổng quản trị của lõi (`/api/thiet-bi`): hành động tạo
  lượt là **nguy hiểm**, cần `xac_nhan` của quản trị (khách gọi vào hàng chờ).
