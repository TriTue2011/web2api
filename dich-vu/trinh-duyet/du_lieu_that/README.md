# Dữ liệu đo thật giao thức Marionette từ Firefox ESR

`marionette_that.json` chứa các khung giao thức GHI LẠI từ một phiên điều khiển Firefox ESR thật. Đây là nguồn sự thật cho test hợp đồng
(`test_marionette_hop_dong_that.py`): máy chủ giả phát lại đúng các khung này, nên test không thể «xanh vì giả định sai» như đêm 18/10/2026
(khung nhị phân thay vì «50:{…}», `GetCookies` bọc `value`, tên lệnh kiểu Selenium).

## 1. Nguồn gốc
- Firefox ESR 153 thật trong container dịch vụ trình duyệt, đo ngày 18/10/2026.
- Trang công khai: `https://example.com/` (phần lớn lệnh) và `https://www.google.com/` (để có cookie thật và đo `GetCookies` có phần tử). Không đăng nhập, không bí mật.
- Khung **đầy đủ**, tiền tố độ dài = số byte UTF-8 thật (test `test_du_lieu_chuan_day_du_khong_cat_va_da_che_truong_nhay_cam` kiểm).

## 2. Cấu trúc
- `nguồn`: mô tả nguồn đo. `đã_che`: liệt kê trường đã che. `khung`: danh sách theo thứ tự phiên.
- Khung chào: `{"huong": "firefox→khách", "thô": "50:{\"applicationType\":\"gecko\",\"marionetteProtocol\":3}"}`.
- Khung lệnh: `{"lệnh": "WebDriver:<Tên>", "tham_số": {...}, "phản_hồi_thô": "<số byte UTF-8>:[1,<id>,<lỗi>,<kết quả>]"}`.
- Tiền tố độ dài là SỐ THẬP PHÂN ASCII rồi dấu «:» (không phải 4 byte nhị phân).

## 3. Trường đã che (kho PUBLIC) và cách che
Phân tích JSON từng khung, che trường nhạy cảm bằng giá trị cố định, rồi **tuần tự hoá lại gọn và tính lại tiền tố** (nên khung đã che vẫn đúng giao thức):
`NewSession` (`moz:profile`, `moz:platformVersion`, `moz:processID`, `moz:buildID`), `GetPageSource` (giá trị HTML rút gọn), `stacktrace` của mọi khung lỗi,
`value` của từng cookie trong `GetCookies`. Tên cookie, `path`, `domain`, các cờ giữ nguyên (là hình dạng thật).

## 4. Hình dạng đã đo (tóm tắt)
Mọi lệnh trả `{"value": …}` trừ `GetCookies` trả **danh sách trực tiếp**. Lỗi trả `{"error": "<mã>", "message": …, "stacktrace": …}` với mã đã đo:
`no such element`, `invalid element state`, `unknown command` (tên lệnh lạ, ví dụ `ClickElement`), `invalid argument` (ví dụ `id` là đối tượng thay vì chuỗi).

## 5. Cách đo lại
Mở TCP tới `127.0.0.1:<cổng Marionette>` của Firefox đang chạy với `--marionette` (cổng 32000–32099 trong container dịch vụ), đọc khung chào, rồi gửi
`<số byte>:[0,<id>,"WebDriver:<Tên>",{tham số}]` và ghi nguyên văn từng phản hồi. Che trường nhạy cảm như mục 3, tính lại tiền tố, rồi chạy lại test.
Thêm lệnh mới vào `marionette.py` mà chưa đo thì `test_moi_lenh_marionette_py_da_do_that` ĐỎ.
