---
name: problem-solving
description: Dùng khi bị kẹt, cách làm thông thường không đủ, hoặc muốn tìm lời giải gọn hơn: tra kiểu kẹt trong bảng rồi dùng đúng kỹ thuật — đảo ngược, mẫu chung, tỉ lệ, đơn giản hoá, giao nhau.
---

# Thoát khỏi điểm kẹt

Kẹt không phải do thiếu cố gắng mà do đang nhìn từ một góc duy nhất. Đổi góc nhìn trước, đổi mã sau.

## Tra cứu nhanh: đang kẹt kiểu nào

| Dấu hiệu | Kỹ thuật | Tệp chi tiết |
|---|---|---|
| Cứ khăng khăng «chỉ có cách này» | Đảo ngược giả thiết | `references/dao-nguoc.md` |
| Cùng một vấn đề xuất hiện ở 3 chỗ trở lên | Nhận diện mẫu chung | `references/mau-chung.md` |
| Không chắc chạy ở quy mô thật | Trò chơi tỉ lệ | `references/tro-choi-ti-le.md` |
| Mã phình to, rẽ nhánh khắp nơi | Chuỗi đơn giản hoá | `references/don-gian-hoa.md` |
| Cách thông thường đều không vừa | Vùng giao nhau | `references/vung-giao-nhau.md` |

Chưa biết mình kẹt kiểu nào thì đọc `references/khi-ket.md` trước.

## Năm kỹ thuật, mỗi cái một câu

- **Đảo ngược:** liệt kê điều «bắt buộc phải đúng», rồi hỏi «nếu ngược lại thì sao».
  Ví dụ: «mọi module đều cần giao diện riêng» → nếu module không có `web/` thì khung chung tự ẩn tab (đúng như kho đang làm).
- **Mẫu chung:** cùng một hình lặp lại ở 3 nơi thì đó là một nguyên tắc — đặt tên cho nó,
  đưa vào lõi một lần thay vì vá ba nơi.
- **Tỉ lệ:** thử ở cực trị — 1 tài khoản và 10.000 tài khoản, 1 luồng và 1.000 luồng.
  Cái gì gãy ở cực trị thì đang sai về mặt cấu trúc.
- **Đơn giản hoá:** tìm một ý đúng khiến nhiều đoạn mã trở nên thừa — «nếu điều này đúng
  thì không cần X, Y, Z nữa». Một trừu tượng mạnh hơn mười mẹo khéo.
- **Giao nhau:** ép hai ý tưởng không liên quan chạm nhau — «nếu coi phiên đăng nhập
  như một module thì sao» — để tìm tính chất mới.

## Luật của kho khi dùng kỹ thuật này

- Mọi ý tưởng đều phải qua skill `debugging`: đo trên chạy thật, không tin trực giác.
- Không đặt tên nhà cung cấp AI trong ví dụ ở phần mô tả lõi; ví dụ về module
  dùng tên giả như `mod-vi-du`.
- Ý tưởng giữ được thì diễn đạt thành nguyên tắc, không thành danh sách từ khoá mới.

Phỏng theo claudekit-skills (MIT, mrgoonie) — đã Việt hoá và chỉnh theo luật kho web2api.
