# Kiểm tra nhiều tầng

Một điểm kiểm duy nhất có thể bị vòng qua bởi đường mã khác, refactor, hoặc mock
trong test. Làm cho lỗi **không thể xảy ra về mặt cấu trúc**, không chỉ «đã sửa».

Bốn tầng, từ ngoài vào trong:

1. **Biên API.** Từ chối dữ liệu vô lý ngay ở cổng: kiểu Pydantic, giới hạn độ dài,
   liệt kê giá trị cho phép. Ví dụ: FastAPI `Query(max_length=...)`, model có `Field(pattern=...)`.
2. **Logic nghiệp vụ.** Bắt trường hợp biên mà biên không thấy: trạng thái không hợp lệ,
   khoá đã bị đánh dấu hỏng, module chưa sẵn sàng.
3. **Rào chắn môi trường.** Bảo vệ khỏi ngữ cảnh nguy hiểm: đường dẫn nằm ngoài thư mục
   cho phép, biến môi trường thiếu, quyền tệp sai.
4. **Log gỡ lỗi.** Khi ba tầng trên đều lọt, log phải đủ để truy vết: ghi giá trị đầu vào
   đã che bí mật, không bao giờ ghi khoá/mật khẩu/cookie nguyên văn.

Mỗi tầng bắt một loại trường hợp khác nhau. Sửa xong thì kiểm tra: còn đường nào
vòng qua được tầng mới không (grep các chỗ gọi hàm đã sửa).
