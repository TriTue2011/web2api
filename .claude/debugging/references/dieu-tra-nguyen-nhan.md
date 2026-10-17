# Điều tra nguyên nhân gốc

Trước khi sửa, phải có một cách tái hiện lỗi mà người khác chạy lại được.

1. **Tái hiện nhỏ nhất.** Viết một test pytest đỏ hoặc một lệnh `curl` chạm đúng điểm lỗi.
   Chưa tái hiện được thì chưa được sửa.
2. **Đọc stack trace ngược.** Từ dòng nổ cuối cùng, đi ngược lên cho tới chỗ dữ liệu
   sai xuất hiện lần đầu. Dòng nổ thường chỉ là nạn nhân.
3. **Hỏi dữ liệu, không hỏi cảm giác.** In giá trị thật tại mỗi tầng
   (`print` tạm thời rồi xoá, hoặc breakpoint) thay vì đoán «chắc là do X».
4. **Kiểm tra giả thiết bằng thí nghiệm.** Đổi một biến một lúc, chạy lại, ghi kết quả.
   Hai lần thử mù liên tiếp mà không tiến triển thì dừng, quay lại bước 1.
5. **Nhớ môi trường test của kho:** `conftest.py` chặn mọi đường ra Internet —
   lỗi «không nối được mạng» trong test là do rào chắn này, không phải lỗi thật.

Kết thúc pha này bằng một câu: «Nguyên nhân gốc là … ở tệp X dòng Y».
