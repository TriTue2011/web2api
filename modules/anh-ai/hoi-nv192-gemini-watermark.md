# Hỏi NV192 — phần gỡ dấu nước (watermark) của nhà cung cấp

**Câu hỏi (1 dòng):** Có port phần gỡ/che dấu nước «ngôi sao» mà Gemini/Flow gắn vào ảnh trả về (`services/gemini_watermark.py` của c2a, được gọi trong `normalize()` của 2 adapter) sang module `anh-ai` không?

**Bối cảnh:** c2a hiện tự động gọi `strip_watermark_b64_items(data, origin=...)` sau mỗi lần Gemini/Flow trả ảnh — dấu nước là nhãn nhận diện sản phẩm AI của nhà cung cấp.

**Phương án:**
- A. Port nguyên (tự động gỡ như c2a đang làm).
- B. Không port; trả ảnh nguyên vẹn từ nhà cung cấp (module hiện tại làm theo B — đã xoá import/lời gọi khi port adapter).
- C. Port nhưng để TẮT mặc định, quản trị tự bật qua cấu hình (rủi ro: bật là vi phạm điều khoản).

**Khuyến nghị: KHÔNG LÀM (chọn B).** Gỡ nhãn của sản phẩm AI là vùng điều khoản dịch vụ của nhà cung cấp; rủi ro khoá tài khoản/khoá API thuộc về chủ máy. Ảnh trả về giữ nguyên watermark. Chủ máy quyết sau — nếu đổi ý, phần này là một hàm bọc duy nhất quanh `normalize()`, thêm sau không ảnh hưởng kiến trúc.
