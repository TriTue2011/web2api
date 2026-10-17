# Cửa xác minh

Trước khi tuyên bố bất kỳ trạng thái nào («test xanh», «dựng được», «xong»),
đi qua đủ 5 bước:

1. **Xác định:** lệnh nào chứng minh được tuyên bố này?
2. **Chạy:** thực thi đầy đủ lệnh đó, mới tinh, không cắt bớt.
3. **Đọc:** toàn bộ đầu ra — đếm số lỗi, xem mã thoát.
4. **Đối chiếu:** đầu ra có khớp tuyên bố không? Khớp thì tuyên bố KÈM bằng chứng;
   không khớp thì báo đúng trạng thái thật.
5. **Rồi mới tuyên bố.**

Bỏ bước nào cũng bằng không xác minh.

Dấu hiệu đỏ — thấy thì dừng:

- Dùng từ «chắc là», «có vẻ», «lẽ ra phải» thay cho kết quả đo được.
- Tỏ vẻ hài lòng trước khi chạy lệnh kiểm.
- Cam kết mà chưa chạy bộ kiểm chuẩn của kho.
- Tin báo cáo của agent nhưng báo cáo thiếu dòng tổng kết và mã thoát
  (xem ngoại lệ ở SKILL.md — thiếu hai thứ đó thì không được tin).

Yêu cầu tối thiểu cho tuyên bố «xong» trong kho này: pytest toàn bộ mã thoát 0,
bandit mức HIGH sạch, ảnh docker dựng được, và đã thử thật bằng `curl`.
