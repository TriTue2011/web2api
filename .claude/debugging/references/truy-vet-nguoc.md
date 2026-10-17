# Truy vết ngược tới điểm phát sinh

Bản năng mách ta sửa ở chỗ lỗi nổ ra. Đó thường là vá triệu chứng.

1. Lấy stack trace đầy đủ, đánh dấu từng khung: khung nào **tạo ra** dữ liệu sai,
   khung nào chỉ **truyền tiếp** nó.
2. Truy tới khung đầu tiên nhận hoặc sinh ra giá trị sai — đó là điểm sửa.
3. Hỏi: vì sao giá trị sai lọt qua được tới đây? Câu trả lời dẫn tới tầng kiểm tra
   còn thiếu (xem `kiem-tra-nhieu-tang.md`).
4. Sửa ở điểm phát sinh, rồi chạy lại test tái hiện ở SKILL.md.

Ví dụ trong kho: lỗi 401 trả về ở `tuyen.py` nhưng nguyên nhân có thể là khoá bị
đánh dấu sai trong `so_module.py` — sửa ở sổ, không sửa ở tuyến trả về.
