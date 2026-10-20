# Module facebook — Facebook (đăng bài Trang)

Đăng bài lên **Trang (Page)** Facebook qua **Graph API chính thức** của Meta, theo
chuẩn thiết bị C1 (`thiet_bi`) của lõi. Mã port từ `services/facebook_page.py`
@ commit `d879b03` của c2a (chỉ đổi lớp kết nối/khuôn module/xác thực).

**Chỉ đăng lên Trang.** Đăng vào **NHÓM không hỗ trợ**: Meta đã gỡ Groups API
từ 22/04/2024; c2a đăng nhóm bằng tài khoản cá nhân qua trình duyệt tự động +
captcha-solver — đường **không chính thức** (rủi ro checkpoint/khoá tài khoản,
vi phạm điều khoản nền tảng) nên **không port**.

## Cách hoạt động

- **Kết nối một lần**: điền `app_id` + `app_secret` (app Meta tự tạo), dán
  `user_token` ngắn hạn từ Graph API Explorer, bấm Cài → module đổi lấy token
  dài hạn (~60 ngày) qua `oauth/access_token` (`fb_exchange_token`), nạp danh
  sách Trang qua `me/accounts` (phân trang đầy đủ), **xoá `user_token` ngắn
  hạn** khỏi cấu hình đã lưu. Page access token lấy từ token người dùng dài
  hạn thực tế không hết hạn (chỉ chết khi đổi mật khẩu/thu hồi quyền).
- Mỗi Trang = một thực thể `facebook.trang_<page_id>`, hành động
  `dang_bai` / `dang_anh` / `dang_video`, **`rui_ro_mac_dinh: "nguy_hiem"`** —
  lõi bắt **quản trị xác nhận** (SEC-004f) trước khi bất cứ bài nào ra công
  chúng. Đây là lý do dùng chuẩn C1 chứ không phải `kenh_ra` (kênh gửi ra
  không có cổng xác nhận cho hành động ghi công khai).
- Thực thể `facebook.ket_noi` (rủi ro «doc»): `liet_ke_trang` (chỉ id + tên,
  không token) và `kiem_tra` (gọi Graph kiểm từng token Trang còn sống).
- **Giới hạn tần suất**: tối thiểu 30 giây giữa hai bài trên cùng một Trang
  (trần tự chọn, chống lỗi Graph 1390008 «đăng quá nhanh»), giữ bằng khoá
  chống đua theo Trang nên các lệnh đăng đồng thời không lọt qua cùng lúc;
  lõi còn áp trần 30 lệnh/phút theo (khoá, thực thể) riêng.
- Lỗi Graph được **dịch tiếng Việt** theo bảng `error_subcode`/`code` (kế
  thừa từ c2a/Postiz); thử lại lỗi thoáng qua CHỈ với GET đọc (429,
  `is_transient`, mã 4/17/32/613, 5xx) — **POST đăng bài KHÔNG thử lại sau
  khi yêu cầu đã gửi** (đăng bài không idempotent: thử lại = đăng TRÙNG
  công khai; lỗi sau gửi trả «không rõ kết quả», dặn người dùng mở Trang
  kiểm tra trước khi đăng lại; chỉ thử lại lỗi chắc chắn chưa gửi như
  không nối được máy chủ). Token hỏng/nội dung vi phạm thì báo ngay,
  không thử lại vô ích. Mọi lời gọi kèm `appsecret_proof`
  (HMAC-SHA256 của token bằng app_secret).

## Đăng ảnh / video

- **Ảnh**: người gọi đưa URL — module **tự tải** qua `app.tai_url_khong_tin`
  (chặn IP riêng/loopback/link-local/metadata đám mây, ghim IP chống DNS
  rebinding, trần 4 MB) và kiểm bằng `app.tep_an_toan.kiem_anh` (đúng
  PNG/JPEG/GIF/WebP), rồi upload bytes lên `/{page}/photos`
  (`published=false`) và gom một bài `/{page}/feed` bằng `attached_media`.
  **Không** chuyển URL thẳng cho Facebook như c2a (Facebook tự kéo có nghĩa
  là URL chưa kiểm đi ra ngoài).
- **Video**: Graph nhận `file_url` để Facebook tự kéo (video không tải về
  máy chủ) — URL vẫn phải qua kiểm tra host **công khai** (`kiem_url_co_ban` +
  `kiem_dns_va_ip` của lõi; LAN/loopback bị từ chối).

## Bảo mật

- Địa chỉ gọi **cố định** `https://graph.facebook.com/v23.0` trong mã — không
  cho cấu hình URL tuỳ ý (chống SSRF).
- Token (Page access token / user token / app_secret):
  - lưu ở trường `bi_mat` (kho 0600, không API nào trả lại);
  - POST: token đi trong **thân**, không trong URL; GET: token ở query (Graph
    yêu cầu) nhưng module cài **bộ lọc log** bỏ mọi dòng của httpx/httpcore
    chứa token, và không bao giờ `str()` ngoại lệ httpx vào log/kết quả;
  - `liet_ke_trang`, `suc_khoe`, kết quả `cai()` chỉ có dạng che `…abcd`.
- Tên Trang từ Facebook là dữ liệu không tin: module bỏ ký tự điều khiển và
  cắt 100 ký tự trước khi đưa vào thực thể.

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `app_id` | Có | Mã app Meta (developers.facebook.com, loại Business). |
| `app_secret` | Có | App Secret của app đó. Lưu bí mật. |
| `user_token` | Không | Token người dùng NGẮN HẠN từ Graph API Explorer (quyền `pages_manage_posts` + `pages_show_list`); chỉ dùng một lần khi Cài, đổi xong bị xoá. |
| `user_token_long` | Không | Token dài hạn ~60 ngày — module TỰ LƯU, không tự nhập. |
| `pages` | Không | Danh sách Trang kèm Page access token — module TỰ LƯU dạng bí mật. |

## Ví dụ dùng

```bash
# Liệt kê Trang đã kết nối (thực thể kết nối, rủi ro «doc»)
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"hanh_dong": "liet_ke_trang", "tham_so": {}}' \
  $W/api/thiet-bi/facebook.ket_noi/hanh-dong

# Đăng bài chữ lên Trang (thực thể nguy_hiem → quản trị phải xác nhận trước;
# lần đầu gọi khi chưa xác nhận sẽ nhận 409 theo cổng SEC-004f của lõi)
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"hanh_dong": "dang_bai", "tham_so": {"noi_dung": "Chào cả nhà!"}, "xac_nhan": true}' \
  $W/api/thiet-bi/facebook.trang_111/hanh-dong
```

## Giới hạn

- **CHƯA kiểm với Facebook thật** (test bằng máy chủ Graph giả cục bộ).
- Không đăng nhóm (Meta gỡ Groups API — xem đầu tệp).
- Không có luồng chat/máy trạng thái soạn bài như c2a (menu `/facebook`,
  `<<<ASK>>>`, dọn rác bài do model viết…) — đó là tầng agent của c2a; ở
  web2api việc đăng đi qua thực thể C1 + cổng xác nhận của lõi.
- Không lên lịch đăng, không sửa/xoá bài đã đăng, không đọc bình luận.
