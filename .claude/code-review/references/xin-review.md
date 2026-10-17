# Xin review qua subagent

Nhờ một subagent soát độc lập để bắt lỗi trước khi nó lan xa. Soát sớm, soát thường xuyên.

**Bắt buộc xin review:** sau mỗi việc lớn, trước khi gộp vào nhánh chính.
**Nên xin:** khi kẹt cần góc nhìn mới, trước khi refactor (lấy mốc so sánh), sau khi sửa lỗi phức tạp.

Brief cho subagent soát phải có đủ:

- **Đã làm gì:** mô tả ngắn gọn thay đổi.
- **Kế hoạch/yêu cầu gốc:** để soát xem có lệch không.
- **Phạm vi diff:** SHA base và SHA head (`git rev-parse HEAD~1`, `git rev-parse HEAD`),
  hoặc danh sách tệp đã đổi.
- **Kỳ vọng trả về:** lỗi theo mức — chặn ngay (sửa lập tức), nên sửa (sửa trước khi đi tiếp),
  ghi nhận (để sau).

Xử lý kết quả: sửa mức «chặn ngay» trước, «nên sửa» trước khi đi tiếp, «ghi nhận»
cho vào việc sau. Review xong vẫn phải qua cửa xác minh ở `cua-xac-minh.md`
trước khi tuyên bố hoàn tất.
