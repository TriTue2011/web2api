# Module may-dang-nhap — Client dịch vụ Đăng nhập tự động

Module client HTTP mỏng kết nối tới container dịch vụ phụ `dich-vu/may-dang-nhap` (FastAPI, cổng 8010, xác thực Bearer).

Module chỉ đóng vai trò cầu nối nội bộ: mọi tác vụ nặng (Chromium, Patchright, màn hình ảo Xvfb, giải captcha âm thanh) và két lưu trữ tài khoản mã hoá AES-256-GCM đều được cách ly hoàn toàn trong container riêng.

---

## 1. Hướng dẫn từng bước triển khai & kết nối

### Bước 1: Khởi động dịch vụ phụ may-dang-nhap
Trong thư mục `dich-vu/may-dang-nhap/`:
```bash
docker compose up -d --build
```
Dịch vụ khởi động với mạng Docker riêng biệt `web2api-may-dang-nhap` và lắng nghe cổng 8010 (nội bộ).

### Bước 2: Đọc khoá xác thực dịch vụ
Dịch vụ tự động sinh khoá Bearer ngẫu nhiên an toàn tại `/khoa/khoa-dich-vu` (quyền 0600):
```bash
docker exec web2api-may-dang-nhap cat /khoa/khoa-dich-vu
```
*(Hoặc sử dụng giá trị khoá đã khai báo trước qua biến `CAPTCHA_SOLVER_API_KEY`).*

### Bước 3: Nối mạng Docker
Đảm bảo container chính của `web2api` có thể kết nối tới container `web2api-may-dang-nhap` (ví dụ cùng nối vào mạng Docker `web2api-may-dang-nhap`, hoặc ánh xạ cổng loopback `127.0.0.1:8010`).

### Bước 4: Cài đặt module may-dang-nhap
Trên giao diện web2api:
- Truy cập **Hệ thống → Module**, tìm ô **may-dang-nhap** và bấm **Cài đặt**.
- Hoặc sử dụng API:
  ```bash
  curl -s -X POST -H "Authorization: Bearer $WEB2API_ADMIN_KEY" \
    http://127.0.0.1:3031/api/nguon-git/cai/may-dang-nhap
  ```

### Bước 5: Cấu hình địa chỉ & khoá dịch vụ
Cài đặt cấu hình kết nối an toàn:
```bash
curl -s -X POST -H "Authorization: Bearer $WEB2API_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cau_hinh": {
      "dich_vu_url": "http://web2api-may-dang-nhap:8010",
      "khoa_dich_vu": "<khoá_đọc_được_ở_bước_2>"
    }
  }' \
  http://127.0.0.1:3031/api/modules/may-dang-nhap/cai
```

### Bước 6: Kiểm tra sức khoẻ kết nối
```bash
curl -s -H "Authorization: Bearer $WEB2API_ADMIN_KEY" \
  http://127.0.0.1:3031/api/modules/may-dang-nhap/suc-khoe
```
Kết quả trả về `{"ok": true, "dich_vu_url": "http://web2api-may-dang-nhap:8010"}`.

---

## 2. Bảng hành động đóng & Bảo vệ an toàn dữ liệu

### 2.1. Bảng hành động công cụ AI (`thuc_hien`)
Chỉ các hành động an toàn sau đây được phép gọi qua công cụ AI (`thuc_hien`):

| Tên hành động | Phương thức | Tuyến đường dịch vụ | Nhóm rủi ro | Các trường cho phép trả ra (Allowlist) |
|---|---|---|---|---|
| `ho_so_danh_sach` | GET | `/v1/profiles` | `doc` | `ok`, `profiles`, `count` |
| `phien_trang_thai` | GET | `/v1/session/{ho_so}/status` | `doc` | `profile`, `loaded`, `pages`, `cookies` (số lượng int), `stale` |
| `dang_nhap_bat_dau` | POST | `/v1/session/auto-login-saved` | `ghi` | `profile`, `started`, `status`, `message`, `state`, `ok` |
| `dang_nhap_trang_thai` | GET | `/v1/session/{ho_so}/auto-login-status` | `doc` | `profile`, `state`, `message`, `started_at`, `updated_at`, `success`, `error`, `ok` |
| `ho_so_dong` | POST | `/v1/session/{ho_so}/close` | `ghi` | `profile`, `closed`, `status`, `message`, `ok` |
| `tai_khoan_da_luu_danh_sach` | GET | `/v1/accounts/saved` | `doc` | `ok`, `accounts` (chỉ trường không bí mật), `count` |

Module cung cấp chuẩn `thiet_bi` (`thuc_the`, `trang_thai`, `thuc_hien`) với hai thực thể:
- `may_dang_nhap.cong_cu_doc` (các hành động đọc: `ho_so_danh_sach`, `phien_trang_thai`, `dang_nhap_trang_thai`, `tai_khoan_da_luu_danh_sach` — rủi ro mặc định `doc`).
- `may_dang_nhap.cong_cu_ghi` (các thao tác ghi: `dang_nhap_bat_dau`, `ho_so_dong` — rủi ro `ghi`, cần xác nhận khi mới nạp theo SEC-004f).
Toàn bộ kết nối HTTP nội bộ được bảo vệ bằng `TransportGhimIp` (chống DNS rebinding), kiểm SSRF chặt chẽ, và đọc stream có trần 1 MiB (`PhanHoiQuaLon`).

### 2.2. Vì sao các tuyến nhạy cảm KHÔNG lộ qua công cụ AI
Các endpoint sau đây của dịch vụ tuyệt đối **không** nằm trong `HANH_DONG` và **không** bao giờ được đưa vào công cụ AI:
- `POST /v1/accounts/saved` (nhận mật khẩu thô và hạt giống TOTP).
- `GET /v1/accounts/saved/{email}/reveal` (giải mã mật khẩu).
- `GET /v1/accounts/saved/{email}/totp` (lấy hạt giống TOTP).
- `DELETE /v1/accounts/saved/{email}` (xoá tài khoản).
- Các endpoint trích xuất token/cookie (`/cookies`, `/token`, `/refresh-jwt`).

**Lý do**: Ngăn chặn rủi ro mô hình AI bị tấn công prompt injection hoặc rò rỉ dữ liệu nhạy cảm ra ngữ cảnh trò chuyện.
Để phục vụ việc quản trị và trao đổi giữa các module nội bộ, module cung cấp các phương thức Python riêng biệt:
- `luu_tai_khoan(email, mat_khau, totp)`: nhận mật khẩu/mã TOTP qua lớp `BiMatTam` và tự động ghi đè bộ đệm bằng `0` ngay sau khi gọi mạng.
- `xoa_tai_khoan(email)`
- `bat_dau_dang_nhap(dich_vu, ho_so)`
- `trang_thai(dich_vu, ho_so)`
- `gui_ma_2fa(dich_vu, ho_so, ma)`
- `lay_cookie(dich_vu, ho_so)`

> [!WARNING]
> Các phương thức Python trên **chưa nối với giao diện/NV144; hiện chỉ gọi được qua API dịch vụ**.
> Ngoài ra, **noVNC mặc định tắt và CHƯA dùng được từ web2api; chỉ truy cập qua SSH tunnel; nhánh cần người chưa hoạt động**.

---

## 3. Tận dụng từ c2a

Module kế thừa toàn bộ cấu trúc tự động hoá đăng nhập từ dự án `chatgpt2api`:
- Cấu trúc đăng nhập đa dịch vụ: Google, Gemini Web, OpenAI / ChatGPT, Claude Web, Grok Web.
- Cơ chế giải captcha âm thanh và Turnstile khi người dùng cài thêm gói bổ trợ.
- Cơ chế dọn hồ sơ chống bận đè (`_don_ho_so_truoc_khi_dang_nhap`).
- Lưu trữ an toàn bằng két SQLite AES-256-GCM.

---

## 4. Các điểm CHƯA kiểm tra

- **Kiểm thử hiện tại thực hiện với dịch vụ GIẢ (DummyPool, hàm đăng nhập giả)**: chưa kiểm tra với các luồng đăng nhập thực tế của bên thứ ba qua mạng ngoài (Google, OpenAI, Anthropic, xAI) do môi trường hộp cát không có mạng.
- Chưa dựng ảnh và chưa kiểm tra khởi động Firefox/Chromium trong container Docker thật (môi trường hộp cát không có Docker daemon).
- Chưa đo đạc tải thực tế khi chạy đồng thời nhiều hồ sơ trình duyệt.
