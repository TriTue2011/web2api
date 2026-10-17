# Tiếp nhận phản hồi review

Phản hồi review là ý kiến kỹ thuật, không phải bài kiểm tra thái độ.

1. **Đọc** hết phản hồi, đừng phản ứng vội.
2. **Hiểu** — diễn đạt lại yêu cầu bằng lời của mình; chưa rõ thì hỏi, hỏi hết một lượt.
3. **Xác minh** — đối chiếu với mã thật: ý kiến có đúng với kho này không?
   Dùng `grep` kiểm tra trước khi tin, kể cả ý kiến nghe rất hợp lý.
4. **Đánh giá** — đúng thì làm, sai thì phản biện bằng lý lẽ kỹ thuật cụ thể
   (tệp, dòng, hành vi đo được). Không đồng ý lấy lệ, không cãi vì tự ái.
5. **Làm** từng ý một, mỗi ý xong chạy test liên quan.

Phân biệt nguồn:

- **Chủ kho / người tin cậy:** hiểu rồi làm, không cần vòng vo xã giao.
- **Nguồn ngoài (agent khác, người lạ):** xác minh kỹ thuật trước, kiểm tra xem có
  làm vỡ chỗ khác không, sai thì bác thẳng.

Quy tắc YAGNI: trước khi làm theo gợi ý «cho đúng chuẩn», `grep` xem chỗ đó có
ai dùng không — không ai dùng thì đừng thêm.
