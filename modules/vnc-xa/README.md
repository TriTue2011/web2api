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
   - Phản hồi: `[{"ho_so": "...", "ten": "...", "loai": "...", "trang_thai": "...", "dang_mo": false, "thu_cong": false, "luc_mo": null}]`

4. **Tạo workspace:** `POST {dich_vu_url}/ho-so` thân `{"ten": "...", "loai": "firefox"}` → `{"ho_so", "ten"}`.
5. **Lấy cookie:** `GET {dich_vu_url}/ho-so/{id}/cookie?mien=<tên miền>` → `{"cookie", "so_cookie"}` (chỉ hồ sơ Firefox).
   Mã 400/404/501 của dịch vụ thành ngoại lệ có thuộc tính `ma` (lõi chuyển tiếp kèm câu của lõi); lỗi khác chỉ nêu mã
   trạng thái — không chép nội dung phản hồi.

`mo` nhận thêm `url` (trang bắt đầu, lõi đã kiểm). Danh sách hồ sơ có thêm `ten`, `dang_mo`, `thu_cong`.

## Giao diện — tab «Trình duyệt»

Trang `web/index.html` (bám thẻ «Workspace trình duyệt» + mục «Tái dùng» của c2a): tạo workspace, chọn trang bắt
đầu (Muse, Google, ChatGPT, Claude, Gemini), «Mở» → liên kết «Vào trình duyệt», «Đóng · giữ phiên», và «Tái dùng
workspace → thêm vào module»: chọn workspace đã đóng + module nhận — trường bí mật + tên miền → «Lấy cookie sang
module». Cookie đi dịch vụ → lõi → module đích, không bao giờ qua trang. Các bước đầy đủ: `dich-vu/trinh-duyet/README.md`
mục «Đăng nhập Muse trên server».

## Cấu hình

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `dich_vu_url` | chuỗi | Có | Địa chỉ gốc HTTP của dịch vụ trình duyệt (được kiểm tra chống SSRF). |
| `khoa_dich_vu` | bí mật | Có | Khoá bí mật dùng cho tiêu đề `Authorization: Bearer <khoá>`. Được bảo mật ở kho riêng của lõi. |

## Tài nguyên

Dịch vụ trình duyệt thật chạy trong container riêng độc lập, không tiêu tốn RAM hay tài nguyên của container lõi web2api.
