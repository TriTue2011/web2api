# OpenAI Codex (OAuth)

Đăng nhập OAuth PKCE vào tài khoản Codex của người dùng, lưu token trong kho
riêng mã hoá của lõi (quyền 0600), tự làm mới token (định kỳ qua vòng
`tự-làm-mới` của lõi + phản ứng khi 401), theo dõi hạn mức từ header
`x-codex-*`/endpoint `/wham/usage`, đánh dấu tài khoản hỏng/deactivated chờ
duyệt. Chat qua Codex Responses API của `chatgpt.com` (`POST
/backend-api/codex/responses`), chuyển đổi định dạng SSE ↔ chat completions
trong module.

Mặc định **TẮT cho tới khi có tài khoản**: module không tự chạy gì khi chưa
cài tài khoản; vòng tự làm mới của lõi chỉ gọi cho tài khoản ĐÃ có.

## Luồng đăng nhập (hành động thiết bị C1, quản trị)

1. `thuc_hien("openaicodex.dang-nhap", "bat_dau_dang_nhap")` → nhận
   `auth_url`: mở trong trình duyệt và đăng nhập.
2. Bộ nghe callback chỉ nghe trên `127.0.0.1:1455` và tự đóng sau khi xong.
   Trình duyệt ở xa không gọi về được → chép URL callback đầy đủ rồi gọi
   `thuc_hien(..., "dan_callback", {"url_callback": "<URL>"})` để đổi mã lấy
   token.

Các hành động trên tài khoản (`openaicodex.tk-<…>`): `lam_moi`, `kiem_han_muc`,
`xoa_tai_khoan` (cần `xac_nhan: "xoa"`), `giu_tai_khoan` (khi deactivated).

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `refresh_token` | Không | Mỗi dòng một refresh_token để thêm tài khoản trực tiếp mà không cần luồng OAuth — lấy từ phiên Codex đã đăng nhập trước đó. Chỉ hiện dạng che. |
| `co_so_url` | Không | Để trống dùng `https://chatgpt.com`. CHỈ dùng cho kiểm thử với máy chủ giả. |

## Chưa kiểm / giới hạn

- Chưa kiểm với tài khoản Codex thật (test bằng máy chủ OAuth/Codex giả).
- Danh sách model (`openaicodex/gpt-5.5`, `openaicodex/gpt-5`) suy từ bản
  tham khảo `chatgpt2api` (c2a); không bịa số hạn mức nhà cung cấp không cho hỏi.
- Phần không port từ bản tham khảo: tự đăng nhập lại qua captcha-solver,
  lệnh chat «xóa <email>/giữ <email>» của admin, xoá dòng `codex_auto_list`
  (cấu hình riêng của c2a). Xem chi tiết trong báo cáo NV173.
