# Nhận diện mẫu chung

Khi cùng một hình xuất hiện ở 3 nơi trở lên, đó không còn là trùng hợp — đó là một
nguyên tắc chưa được đặt tên.

1. Ghi ra 3 nơi thấy hình đó (tệp, dòng).
2. Rút dạng trừu tượng: bỏ chi tiết riêng, giữ lại cái chung.
3. Đặt tên, đưa vào một chỗ duy nhất (thường là lõi), ba nơi cũ gọi lại nó.

Ví dụ trong kho: «chuyển tài khoản khi gặp lỗi» lặp ở mọi module dạng API —
dạng trừu tượng là «phân loại lỗi bằng cấu trúc rồi thử nguồn kế» — nên nằm ở
bộ chuyển tiếp dùng chung, không copy vào từng module.

Cảnh báo: đừng trừu tượng hoá khi mới thấy 2 lần. Lần thứ ba mới đủ bằng chứng.
