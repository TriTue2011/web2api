# Module rclone — cầu nối lưu trữ đám mây (NV161)

Cầu nối liệt kê/đọc/tải về/tải lên/xoá/chuyển tệp trên 150+ backend của rclone
(Google Drive, S3/R2, OneDrive, WebDAV, SFTP…) qua dịch vụ `dich-vu/rclone`
chạy trong container phụ (rclone v1.75.2). Cấu hình rclone (token R2/S3/Drive)
nằm ở **dịch vụ**, không qua module.

Port từ c2a (`/opt/claude-c2a/chatgpt2api` @ d879b03, `services/rclone_service.py`
+ `api/rclone.py`) theo LUẬT PORT: giữ nguyên ý ba ràng buộc an toàn (đường cục
bộ khoá `resolve()`; không bao giờ qua shell; tệp cấu hình 0600 tách khỏi
log/phản hồi); khác c2a: nhị phân rclone chạy trong container riêng thay vì
trong tiến trình lõi.

## Thực thể và hành động

Mỗi thực thể ĐÚNG một mức rủi ro (lõi tính rủi ro theo thực thể, không theo
hành động). Cần `ghi_bat` bật cho mọi hành động ghi/xoá (mặc định TẮT; tắt
thì báo `chua_bat_ghi`, không gọi mạng):

- `rclone.doc` — đọc/tải về (rủi ro `doc`): `liet_ke`, `doc_chu`, `tai_ve`,
  `remotes`, `kiem_tra`, `config_xem`, `drive_start`.
- `rclone.ghi` — tải lên/chuyển (rủi ro `ghi`): `gui_len`, `chuyen_tep`.
- `rclone.xoa` — xoá tệp/remote (rủi ro `nguy_hiem`): `xoa`, `xoa_remote`.
- `rclone.cau_hinh` — cấu hình remote/khoá (rủi ro `nguy_hiem`):
  `them_remote`, `config_ghi`, `drive_exchange`, `luu_khoa_json`.

Thực thể `nguy_hiem` luôn đòi xác nhận của lõi TỪNG LẦN — kể cả sau khi
quản trị đã xác nhận thực thể một lần (đây là lý do tách thực thể).

`gui_len` KHÔNG nhận nội dung tệp qua tham số (lõi trần tham số 8 KB):
tham số là `duong_dan_tep` — đường dẫn trên máy chạy web2api, bắt buộc nằm
trong thư mục `thu_muc_len` đã khai (resolve hết symlink); module đọc tệp
rồi đẩy byte qua đường tệp riêng của dịch vụ. Chưa khai `thu_muc_len` thì
`gui_len` báo `chua_khai_thu_muc_len`.

`config_xem` trả cấu hình **đã che theo cấu trúc** (mặc định che mọi giá
trị thành `•••`, chỉ siêu dữ liệu an toàn như `type`/`region`/`provider`
mới hiện); token thật không bao giờ rời dịch vụ. Loại remote + tham số
remote là BẢNG ĐÓNG ở dịch vụ (chỉ drive/s3/webdav/onedrive/dropbox/box/
mega/ftp/sftp/http với đúng tham số của từng loại; cấm `local`, `sftp` kèm
`ssh`, remote chồng `alias`/`union`/`crypt`…) — áp cho cả `them_remote` và
`config_ghi`. Ghi `rclone.conf` qua `config_ghi`/`drive_exchange`: hỏng
thì dịch vụ giữ bản cũ.

## Cài đặt

1. Dựng và chạy dịch vụ (xem `dich-vu/rclone/README.md`), nhớ đặt `RCLONE_KHOA`.
2. Cài module, cấu hình: `dich_vu_url` (mặc định `http://127.0.0.1:8702` —
   chỉ loopback/mạng nội bộ, địa chỉ công khai bị chặn), `khoa_dich_vu` =
   khoá vừa sinh (≥ 24 ký tự), `ghi_bat` = `0` (mặc định), `thu_muc_len` =
   thư mục trên máy web2api được phép tải lên (để trống nếu không dùng
   `gui_len`).
3. Khai remote: qua `config_ghi` (dán `rclone.conf` thật), `them_remote`
   (S3/R2/WebDAV/SFTP không OAuth), hoặc `drive_start`/`drive_exchange`
   (Google Drive qua mã cấp quyền).

## Giới hạn

- Chưa kiểm với dịch vụ đám mây thật (test bằng nhị phân giả).
- `tai_ve`/`gui_len` ≤ 25 MB (trần ở cả dịch vụ lẫn module); `doc_chu` ≤ 2 MB
  văn bản.
- `drive_exchange` cần dịch vụ ra được Internet tới `oauth2.googleapis.com`.
