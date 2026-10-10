## Thay đổi này làm gì

<!-- Một đoạn ngắn: việc gì thay đổi và vì sao. Liên kết issue nếu có (Closes #...). -->

## Danh sách kiểm

- [ ] Test đạt: `python -m pytest test -q` thoát với mã 0 (dán dòng tổng kết nếu có thể).
- [ ] Bandit không báo mức cao: `bandit -r app modules -lll` thoát với mã 0.
- [ ] README đã cập nhật nếu thêm module, đường API hoặc biến môi trường (module mới cần cả tiểu mục ở mục 6 lẫn dòng ở bảng «Trạng thái hiện tại»; có test `test/test_web2api_readme.py` bắt lệch).
- [ ] Không lộ bí mật: không khoá API, token, khoá quản trị, địa chỉ hay dữ liệu thật của nhà trong mã, test, ảnh chụp hay nhật ký (kho này công khai).

## Ba câu hỏi lớp lỗi (bắt buộc khi sửa lỗi; với thay đổi khác ghi «không áp dụng»)

1. **Lớp lỗi nào?** (ví dụ: khớp chuỗi bắt nhầm / đọc sai kho dữ liệu / không kiểm chứng kết quả / …)
2. **Còn chỗ nào cùng lớp?** (đã đo bằng `grep`, ghi lệnh và số kết quả — đừng đoán)
3. **Sửa theo nguyên tắc hay theo danh sách?** (thêm danh sách từ khoá chỉ chấp nhận khi nêu được vì sao không có nguyên tắc nào thay thế)
