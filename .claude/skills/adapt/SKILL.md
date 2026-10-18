---
name: adapt
description: Rà soát giao diện tính năng có sẵn của web2api trên điện thoại/máy tính bảng — vùng chạm, cỡ chữ, cột đơn, safe area, không phụ thuộc hover. Dùng khi kiểm giao diện trên màn hình nhỏ; không dùng để thiết kế lại.
---

# Rà soát giao diện trên điện thoại/máy tính bảng

«Chỉ rà soát và sửa lỗi; không đổi diện mạo so với demo của chủ kho.»

Đây là **phiếu rà soát**, không phải hướng dẫn thiết kế lại. Chỉ đánh dấu lỗi
so với các mục dưới đây và sửa đúng chỗ lỗi — giữ nguyên bố cục, màu sắc,
kiểu chữ của demo hiện hành.

## 1. Vùng chạm ≥ 44px

- Mọi nút, liên kết, ô chọn, biểu tượng bấm được phải có vùng chạm tối thiểu
  **44×44 CSS px**, kể cả trên điện thoại.
- Khoảng cách giữa các vùng chạm kề nhau đủ để không bấm nhầm.
- Không thu nhỏ nút để «vừa hàng» trên màn hình nhỏ — thu gọn nội dung xung quanh thay vì thu nút.

## 2. Chữ ≥ 16px, không zoom ép buộc

- Chữ nội dung chính tối thiểu **16px**; chữ phụ không dưới 12px.
- Trên iOS, ô nhập chữ dưới 16px khiến trình duyệt tự zoom — coi đó là lỗi.

## 3. Cột đơn trên điện thoại

- Màn hình hẹp (dưới ~768px): một cột duy nhất, xếp dọc; không để hai cột
  chen nhau hay chữ tràn.
- Bảng nhiều cột chuyển thành thẻ (card) xếp dọc, giữ đủ thông tin, không giấu
  cột quan trọng.
- Nội dung ưu tiên lên trước; nội dung phụ cho vào `<details>/<summary>` hoặc tab.

## 4. Thụt lề cha–con rõ ràng

- Danh sách lồng nhau, cây thư mục, luồng hội thoại phải có thụt lề đủ nhìn
  thấy quan hệ cha–con ngay trên màn hình hẹp.
- Không dùng chỉ màu sắc hoặc khoảng cách mờ nhạt để phân cấp.

## 5. Safe area của điện thoại

- Nội dung không chui vào tai thỏ, góc bo, thanh home: dùng
  `env(safe-area-inset-*)` cho lề trên/dưới/trái/phải.
- Thẻ `<meta name="viewport">` phải có `viewport-fit=cover` nếu giao diện tràn viền.

## 6. Không phụ thuộc hover

- Mọi thông tin và thao tác quan trọng phải tới được **bằng chạm**, không chỉ
  bằng hover: không giấu nút sau hover, không dùng tooltip hover làm kênh duy nhất.
- Trên thiết bị cảm ứng dùng trạng thái `:active` thay cho `:hover`.

## 7. Điều hướng trên màn hình nhỏ

- Điều hướng phức tạp thu gọn thành menu hamburger/drawer hoặc thanh điều hướng
  dưới; giữ nút quay lại trong luồng.
- Không giấu chức năng cốt lõi trên điện thoại: cái gì quan trọng trên máy tính
  thì phải dùng được trên điện thoại.

## Cách rà soát

1. Mở trang trên điện thoại thật (chủ kho dùng điện thoại nhiều) hoặc DevTools
   ở 360px; ghi lại: thiết bị thật hay giả lập.
2. Chạm thử mọi nút/liên kết: bấm trúng ngay lần đầu, không bấm nhầm nút kề.
3. Xoay ngang/dọc: không có thanh cuộn ngang, không chữ tràn.
4. Gõ vào mọi ô nhập: không bị zoom ép buộc, bàn phím không che nút gửi.
5. Ghi kết quả theo từng mục 1–7: **đạt / lỗi** (kèm ảnh chụp hoặc dòng mô tả),
   rồi mới sửa. Sửa xong kiểm lại đúng mục đã lỗi.

**Đã bỏ khỏi nguồn:** phần email, in ấn và native (`adapt.native`) — kho web2api
chỉ có giao diện web, không cần. Phần «thiết kế lại cho ngữ cảnh mới»
(desktop→mobile redesign) cũng bỏ vì ràng buộc kho: giao diện phải giống y hệt demo.
