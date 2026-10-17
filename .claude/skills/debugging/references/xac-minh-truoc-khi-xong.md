# Xác minh trước khi tuyên bố xong

Tuyên bố «xong» mà không có bằng chứng chạy thật là nói dối, không phải hiệu quả.

**Luật sắt: không có bằng chứng xác minh mới thì không được tuyên bố hoàn tất.**

Cửa xác minh, theo đúng thứ tự:

1. **Xác định** lệnh nào chứng minh được tuyên bố (ví dụ: «test xanh» → `pytest test -q -p no:cacheprovider`).
2. **Chạy** đầy đủ lệnh đó, mới tinh, không cắt bớt.
3. **Đọc** toàn bộ đầu ra: đếm số lỗi, xem mã thoát.
4. **Đối chiếu** đầu ra với tuyên bố — khớp thì tuyên bố KÈM bằng chứng, không khớp
   thì báo đúng trạng thái thật.
5. Chỉ sau đó mới được nói «xong».

Bỏ qua bất kỳ bước nào cũng bằng không xác minh. Các từ «chắc là», «có vẻ»,
«lẽ ra phải» trong báo cáo là dấu hiệu đỏ: dừng lại, chạy lệnh, rồi viết tiếp.
