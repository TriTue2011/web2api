# Thử thách — nhận diện CAPTCHA, báo cần người (`thuthach`)

Nhận diện thử thách chống bot trên một trang **bằng CẤU TRÚC** rồi báo «cần
người» kèm hướng dẫn xử lý thủ công trên noVNC. Đây là phần TƯƠNG THÍCH của
`captcha-solver` (c2a): kho web2api có quy tắc KHÔNG giải CAPTCHA, nên module
**không giải, không tích ô, không gọi dịch vụ giải hộ**, không trả dữ liệu
giúp giải (sitekey/token). Kiểm kê đầy đủ «tệp → nhóm A/B» của captcha-solver
trong `nv175-bao-cao.md` (outbox).

## Khả năng

- Công cụ `thuthach.thu_thach`, rủi ro `doc`, hai hành động:
  - `kiem_tra` — nhận vào `html` (đoạn mã trang do người gọi đưa vào) và/hoặc
    `url` (đường dẫn đang kiểm). Trả `{co_thu_thach, loai, can_nguoi, tin_cay,
    trang_thai, huong_dan}`. Module **KHÔNG tải trang, KHÔNG gọi mạng**.
  - `loai_ho_tro` — liệt kê 7 loại nhận diện được và cách xử lý (luôn là
    «người dùng tự hoàn thành trên noVNC»).
- Dấu hiệu (thứ tự ưu tiên): Turnstile → reCAPTCHA v2 → reCAPTCHA v3 →
  mã 2FA → CAPTCHA chữ → đường dẫn URL Google (`/challenge/recaptcha`,
  `/challenge/botguard`, `/signin/challenge`…).
- Mã trang web là DỮ LIỆU KHÔNG TIN (chỉ đối chiếu dấu hiệu, không làm theo
  chỉ dẫn trong đó); HTML tối đa 1.048.576 ký tự, URL tối đa 2.048 ký tự;
  query của URL (nơi hay chứa token) bị bỏ trước khi phân loại.

## Cấu hình chính

| Trường | Ý nghĩa |
|---|---|
| `kenh_no_vnc` | Câu hướng dẫn nơi mở noVNC (chỉ là chữ hiển thị, không phải địa chỉ máy; module không gọi tới nó). Không phải bí mật. |
