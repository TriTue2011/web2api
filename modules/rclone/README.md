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

- `rclone.kho` — đọc/tải về (rủi ro `doc`, mặc định): `liet_ke`, `doc_chu`,
  `tai_ve`, `remotes`, `kiem_tra`, `config_xem`, `drive_start`.
- `rclone.ghi` — cần `ghi_bat` bật (mặc định TẮT): `gui_len`, `chuyen_tep`
  (rủi ro `ghi`); `xoa`, `them_remote`, `xoa_remote`, `config_ghi`,
  `drive_exchange`, `luu_khoa_json` (rủi ro `nguy_hiem`). Tắt thì báo
  `chua_bat_ghi`, không gọi mạng.

`config_xem` trả cấu hình **đã che** (mọi giá trị bí mật thành `•••`); token thật
không bao giờ rời dịch vụ. Ghi `rclone.conf` qua `config_ghi`/`drive_exchange`:
hỏng thì dịch vụ giữ bản cũ.

## Cài đặt

1. Dựng và chạy dịch vụ (xem `dich-vu/rclone/README.md`), nhớ đặt `RCLONE_KHOA`.
2. Cài module, cấu hình: `dich_vu_url` (mặc định `http://127.0.0.1:8702`),
   `khoa_dich_vu` = khoá vừa sinh (≥ 24 ký tự), `ghi_bat` = `0` (mặc định).
3. Khai remote: qua `config_ghi` (dán `rclone.conf` thật), `them_remote`
   (S3/R2/WebDAV/SFTP không OAuth), hoặc `drive_start`/`drive_exchange`
   (Google Drive qua mã cấp quyền).

## Giới hạn

- Chưa kiểm với dịch vụ đám mây thật (test bằng nhị phân giả).
- `tai_ve`/`gui_len` qua API ≤ 25 MB; `doc_chu` ≤ 2 MB văn bản.
- `drive_exchange` cần dịch vụ ra được Internet tới `oauth2.googleapis.com`.
