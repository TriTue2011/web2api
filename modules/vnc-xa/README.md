# Module Trình duyệt từ xa (noVNC)

Module điều khiển và quản lý hồ sơ trình duyệt từ xa thông qua noVNC để người dùng đăng nhập tay (OTP/captcha) và lưu giữ phiên đăng nhập trong các hồ sơ bền vững.

## Hợp đồng dịch vụ trình duyệt thượng nguồn

Module kết nối tới dịch vụ trình duyệt riêng (chạy ở container độc lập `dich-vu/trinh-duyet/` hoặc máy chủ trong LAN) qua giao thức HTTP:

1. **Mở hồ sơ:**
   - Tuyến: `POST {dich_vu_url}/ho-so/mo`
   - Tiêu đề: `Authorization: Bearer <khoa_dich_vu>`
   - Thân yêu cầu: `{"ho_so": "<mã-hồ-sơ>", "loai": "<chromium|firefox|...>"}`
   - Phản hồi: `{"ho_so": "<mã-hồ-sơ>", "loai": "<loại>", "dich": "http://[w2a:<bí-mật>@]<máy-chủ-novnc>:<cổng>"}` —
     userinfo (nếu có) là bí mật riêng của hồ sơ; lõi tách ra thành Basic auth, ghim IP lúc mở, không ghi ra đâu cả.

2. **Đóng hồ sơ:**
   - Tuyến: `POST {dich_vu_url}/ho-so/{id}/dong`
   - Tiêu đề: `Authorization: Bearer <khoa_dich_vu>`
   - Thân yêu cầu: `{"giu_phien": true|false}`
   - Phản hồi: `{"ok": true}`

3. **Danh sách hồ sơ:**
   - Tuyến: `GET {dich_vu_url}/ho-so`
   - Tiêu đề: `Authorization: Bearer <khoa_dich_vu>`
   - Phản hồi: `[{"ho_so": "...", "loai": "...", "trang_thai": "...", "luc_mo": "..."}]`

## Cấu hình

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `dich_vu_url` | chuỗi | Có | Địa chỉ gốc HTTP của dịch vụ trình duyệt (được kiểm tra chống SSRF). |
| `khoa_dich_vu` | bí mật | Có | Khoá bí mật dùng cho tiêu đề `Authorization: Bearer <khoá>`. Được bảo mật ở kho riêng của lõi. |

## Tài nguyên

Dịch vụ trình duyệt thật chạy trong container riêng độc lập, không tiêu tốn RAM hay tài nguyên của container lõi web2api.
