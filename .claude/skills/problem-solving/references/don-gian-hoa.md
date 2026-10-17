# Chuỗi đơn giản hoá

Phức tạp phình to thường do thiếu một ý đúng, không phải thiếu mã.

1. Liệt kê các rẽ nhánh/trường hợp đặc biệt đang tồn tại.
2. Hỏi: «nếu điều X đúng, thì những cái nào trở nên thừa?» — lặp cho tới khi không
   bớt được nữa.
3. Ý còn lại phải diễn đạt được trong một câu. Không diễn đạt được thì chưa phải ý đúng.

Ví dụ trong kho: thay vì mỗi module tự viết cách che khoá trong log,
một nguyên tắc «bí mật chỉ hiện dạng che …abcd, do `kieu` trong manifest quyết»
khiến mọi đoạn che tay trở nên thừa.

Kết quả mong muốn: ít mã hơn, ít nhánh hơn, và câu giải thích ngắn hơn.
