---
name: debugging
description: Dùng khi gặp lỗi, test đỏ hoặc hành vi bất ngờ: điều tra nguyên nhân gốc trước khi sửa, đo chỗ cùng lớp bằng grep, và kết thúc bằng ba câu hỏi lớp lỗi của kho.
---

# Gỡ lỗi

Nguyên tắc sắt: **không sửa khi chưa biết nguyên nhân gốc.** Vá triệu chứng chỉ dời lỗi sang chỗ khác.

## Bốn pha

1. **Điều tra nguyên nhân gốc** — tái hiện lỗi bằng test hoặc lệnh thật, đọc stack trace từ điểm nổ ngược về điểm bắt đầu. Chi tiết: `references/dieu-tra-nguyen-nhan.md`.
2. **Truy vết ngược** — lần theo chuỗi gọi cho tới chỗ dữ liệu sai xuất hiện lần đầu; sửa ở đó, không sửa chỗ nó nổ. Chi tiết: `references/truy-vet-nguoc.md`.
3. **Kiểm tra nhiều tầng** — chỗ dữ liệu đi qua nào cũng có rào chắn: biên API, logic nghiệp vụ, rào chắn môi trường, log gỡ lỗi. Chi tiết: `references/kiem-tra-nhieu-tang.md`.
4. **Xác minh trước khi xong** — chạy lại bằng chứng gốc, đọc mã thoát, rồi mới tuyên bố. Chi tiết: `references/xac-minh-truoc-khi-xong.md`.

## Luật của kho (bắt buộc)

- **Sửa lớp lỗi, không vá triệu chứng.** Thấy lỗi là hỏi ngay: nó thuộc lớp nào?
- **Không thêm danh sách từ khoá** để vá lỗi. Danh sách luôn thiếu; chỉ chấp nhận khi chứng minh được không có nguyên tắc nào thay thế.
- Ví dụ minh hoạ dùng `pytest`, FastAPI, Python — không dùng ví dụ của hệ sinh thái khác.

## Ba câu hỏi lớp lỗi — trả lời trước mọi bản sửa, và ghi vào nội dung commit

1. Lỗi này thuộc **lớp** nào?
2. Còn chỗ nào cùng lớp? **Đo bằng `grep`, đừng đoán** — ghi lệnh và số kết quả. Ví dụ:
   `grep -rn "except Exception" app/ modules/ | wc -l`
3. Sửa theo **nguyên tắc** hay theo **danh sách**?

## Đo trên chạy thật

Test chỉ chứng minh mã chạy đúng như mình nghĩ, không chứng minh mình nghĩ đúng.
Chạy test hoặc lệnh thật, đọc mã thoát, dán kết quả — chưa kiểm được thì nói «chưa kiểm», đừng tuyên bố xong.

Phỏng theo claudekit-skills (MIT, mrgoonie) — đã Việt hoá và chỉnh theo luật kho web2api.
