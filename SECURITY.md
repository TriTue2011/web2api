# Chính sách bảo mật

## Báo lỗ hổng — báo RIÊNG TƯ, đừng mở issue công khai

Kho này công khai, nên issue và pull request ai cũng đọc được ngay. Phát hiện lỗ hổng thì báo bằng
**GitHub Security Advisories**:

1. Vào https://github.com/TriTue2011/web2api/security/advisories/new
   (hoặc tab **Security** của kho → **Report a vulnerability**).
2. Mô tả: phiên bản web2api (`phien_ban` ở `/health` hoặc tệp `VERSION`), cách chạy, các bước tái hiện, và
   tác động bạn thấy. **Đừng đính kèm khoá hay dữ liệu thật** — dùng khoá giả hoặc đã thu hồi.

Nếu bạn lỡ làm lộ khoá thật của chính mình (trong issue, nhật ký, ảnh chụp), hãy **thu hồi khoá đó ngay** ở
nhà cung cấp trước, rồi mới xoá nội dung — lộ ở kho public thì coi như đã lộ, xoá không rút lại được.

## Phạm vi

Thuộc phạm vi:

- Lõi web2api: `app/`, trang `web/`, xác thực khoá quản trị, lưu và che bí mật (khoá API, token), nguồn
  module từ git (tải và kiểm toàn vẹn, cài, tự cài), module từ xa (chặn đích nguy hiểm), phục vụ giao diện
  của module ở `/m/<id>/`.
- Các module trong `modules/` của kho này.
- Ảnh Docker `ghcr.io/tritue2011/web2api` và tệp dựng ảnh (`Dockerfile`, `docker-compose.yml`).

Ví dụ điều đáng báo: vượt xác thực, lộ khoá API hoặc khoá quản trị qua API/nhật ký/thông báo lỗi, đọc tệp
ngoài thư mục cho phép, module từ xa trỏ được tới địa chỉ nội bộ bị cấm, cài được mã không nằm trong nguồn
git đã cấu hình.

Ngoài phạm vi:

- **Mã của module bạn tự cài từ kho git khác.** Module chạy trong tiến trình lõi với quyền như lõi (README
  mục 7 nói rõ) — ai đẩy được lên nhánh git được theo dõi thì chạy được mã trong web2api. Đó là hợp đồng
  thiết kế, không phải lỗ hổng; đừng theo dõi kho bạn không tin.
- Lỗi của dịch vụ bên thứ ba (Google, GitHub, Cloudflare).
- Cấu hình không an toàn do người vận hành chọn, ví dụ mở cổng web2api ra Internet bằng `http://` không
  HTTPS, hoặc để lộ khoá quản trị.
- Tấn công từ chối dịch vụ cần lượng lớn yêu cầu.

## Cam kết phản hồi

Đây là dự án cá nhân, không có đội trực. Mục tiêu hợp lý, không phải hợp đồng:

- Xác nhận đã nhận báo cáo trong vòng **7 ngày**.
- Cho bạn biết đánh giá ban đầu (có phải lỗ hổng không, mức độ) trong vòng **14 ngày**.
- Lỗ hổng được xác nhận: sửa và phát hành bản vá sớm nhất có thể; lỗi nghiêm trọng ưu tiên trước. Thông tin
  được công bố (kèm ghi nhận người báo, nếu bạn muốn) sau khi đã có bản vá.

## Phiên bản được hỗ trợ

Dự án còn ở giai đoạn khung (`0.1.0-khung`): **chỉ bản mới nhất trên nhánh `main` và ảnh `latest` được hỗ trợ**.
Bản vá không được chuyển ngược về các phiên bản cũ; hãy nâng cấp theo README mục 6 («Cập nhật lõi»).
