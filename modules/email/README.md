# Email — kênh gửi/nhận thư (module `email`)

Kênh email hai chiều cho web2api: **gửi ra** qua SMTP (chuẩn `kenh_ra`) và
**nhận tin vào** qua IMAP hỏi vòng (chuẩn `hoi_vong`, mỗi 60 giây một lượt;
lõi tự lùi dần khi lỗi). Port từ kênh email của c2a
(`services/email_channel.py` @ d879b03) — chỉ đổi lớp kết nối/khuôn
module/xác thực, xem mục «Tận dụng từ c2a» trong báo cáo NV154.

## Cài đặt

1. Xem thông báo: `curl -s -H "Authorization: Bearer $K" $W/api/modules/thong-bao` — có `"id": "email"`.
2. Tải về: `curl -s -X POST -H "Authorization: Bearer $K" $W/api/nguon-git/cai/email`
3. `POST /api/modules/email/cai` với thân `{"cau_hinh": {...}}`:

```json
{
  "tai_khoan": "[{\"id\": \"chinh\", \"label\": \"Gmail chính\", \"enabled\": true, \"imap_host\": \"imap.gmail.com\", \"imap_port\": 993, \"smtp_host\": \"smtp.gmail.com\", \"smtp_port\": 465, \"user\": \"ban@example.com\", \"security\": \"ssl\", \"verify_ssl\": true, \"allowed_senders\": [\"sep@example.com\", \"@congty.vn\"], \"mark_seen\": true, \"max_body_chars\": 6000, \"poll_seconds\": 60}]",
  "mat_khau": ["mat-khau-ung-dung-16-ky-tu"],
  "nguoi_nhan_cho_phep": "",
  "mang_noi_bo": ""
}
```

- `tai_khoan`: mảng JSON các hộp thư. **Không ghi mật khẩu vào đây** — mật khẩu
  ở trường `mat_khau`, **cùng thứ tự**.
- `mat_khau`: mỗi dòng một mật khẩu (Gmail bật 2FA thì dùng **App Password**
  16 ký tự, không dùng mật khẩu đăng nhập). Lưu riêng trong kho bí mật của lõi
  (0600), không API nào trả lại.
- `allowed_senders`: **rỗng = KHÔNG nhận thư của ai** (đóng mặc định).
  Khớp chính xác địa chỉ hoặc theo `@domain`; `"*"` = nhận mọi người (không
  khuyến nghị).
- `security`: `ssl` (IMAP 993 / SMTP 465) | `starttls` (chỉ SMTP 587).
  Chế độ `plain` **đã bị loại**: chỉ cổng TLS được chấp nhận — mật khẩu
  không bao giờ đi trần (ép TLS ở tầng cấu hình).
- `verify_ssl`: mặc định `true` (kiểm chứng chỉ). Chỉ tắt cho máy chủ nội bộ
  tự ký khi bạn hiểu rủi ro nghe lén.
- `nguoi_nhan_cho_phep`: để trống = gửi được cho mọi địa chỉ hợp lệ.
- `mang_noi_bo`: để trống = máy chủ phải qua kiểm `app/tu_xa.py` (chặn
  loopback/link-local/metadata đám mây — chống SSRF). Ghi `"co"` CHỈ khi dùng
  máy chủ thư nội bộ không có DNS công khai; bạn tự chịu rủi ro.

## Tin vào → tin ra

Thư mới từ người gửi được phép được chuyển cho lõi qua `kenh_vao.nhan_tin`
(văn bản đã lọc: HTML thành chữ, đính kèm chỉ trích văn bản ≤ 4000 ký tự/tệp,
tối đa 5 tệp, tệp > 8 MB bị bỏ, KHÔNG ghi đĩa). Lõi phân quyền theo người gửi
như mọi kênh khác; trả lời dùng `gui_tin` của module (đích = hộp thư đã khai).

## ⚠️ Nguy cơ giả mạo người gửi (đọc kỹ trước khi nhận thư từ Internet)

Module chỉ tin địa chỉ `From:` sau khi lọt qua các lớp sau: danh sách
`allowed_senders`, thư có đúng **một** header `From:` (thư nhiều `From:` bị
bỏ), và — khi máy chủ thư phía trên có gắn header `Authentication-Results` —
kiểm kết quả **SPF/DKIM** (fail → bỏ thư). Nếu máy chủ phía trên của bạn
**không** lọc và không gắn kết quả SPF/DKIM, kẻ tấn công trên Internet vẫn
có thể **giả mạo** địa chỉ `From:` khớp danh sách cho phép: ĐỪNG coi thư
nhận qua kênh này là bằng chứng danh tính khi máy chủ chưa lọc SPF/DKIM
trước. Thư kích thước > 2 MB bị bỏ trước khi tải.

## Dừng là dừng hẳn

- Gỡ module → `dung()` dừng vòng hỏi vòng ngay; lõi cũng ngừng gọi `hoi_vong`.
- Hộp thư lỗi 10 lần liên tiếp → dừng hẳn hộp đó, báo **một lần** trong log và
  `suc_khoe`; cài lại cấu hình để chạy tiếp.
- Khởi động lại không phát lại thư đã xử lý: UID IMAP đã thấy lưu theo hộp
  thư kèm **UIDVALIDITY** ở `<WEB2API_DATA>/bi_mat/mo-dun/email/da_thay.json`
  (0600), không lưu nội dung thư. UIDVALIDITY đổi (hộp bị tạo lại) → sổ bắt
  đầu lại cho hộp đó.

## Sự cố thường gặp

- `máy chủ ...: bị chặn: loopback` — máy chủ nội bộ: đặt `mang_noi_bo: "co"`.
- `Sai tài khoản hoặc mật khẩu. Cần App Password...` — Gmail/Outlook bật 2FA:
  tạo App Password, không dùng mật khẩu đăng nhập.
- `không nhận thư của ai` — kiểm tra `allowed_senders` (rỗng = chặn hết) và
  `enabled: true`.

## Giới hạn

- Chỉ đọc văn bản đính kèm (không đọc PDF/Office như bản c2a — module không có
  pipeline tài liệu).
- Chưa kiểm với IMAP/SMTP thật, Gmail thật, hộp thư thật (chỉ kiểm với máy chủ
  giả cục bộ).
