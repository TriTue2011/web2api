# Dịch vụ rclone — nhị phân rclone trong container phụ (NV161)

Một dịch vụ HTTP nhỏ chạy **riêng** cạnh web2api: nhận các lệnh lưu trữ đám mây
qua `POST /lenh` (tập lệnh đóng), gọi nhị phân rclone (Go, giấy phép MIT) rồi trả
kết quả. web2api **không** nhúng nhị phân vào ảnh lõi (quyết định chủ máy:
«module nặng ở container riêng»).

## Đã đọc về rclone (ghi theo yêu cầu spec)

- Nguồn: `github.com/rclone/rclone`, giấy phép **MIT**. Tải nhị phân từ
  `downloads.rclone.org` (không dùng ảnh Docker Hub của rclone để còn kiểm được
  checksum theo bản phát hành).
- Bản **v1.75.2** (phiên bản mới nhất tính đến 20/10/2026, `downloads.rclone.org/version.txt`).
- SHA-256 của bản phát hành: tự tải zip về, tính lại bằng `sha256sum`, đối chiếu
  **khớp** tệp `SHA256SUMS` chính thức của nhà phát hành, rồi ghim vào Dockerfile:
  - amd64: `349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702`
  - arm64: `7e1e8d69654941b7b7df84ee74c5f7fb09cce7d5496947fb7bf16b55ff427d10`
  Cách đo lại: `curl -fsSL https://downloads.rclone.org/v1.75.2/rclone-v1.75.2-linux-amd64.zip | sha256sum`.
- Ảnh nền `python:3.13-slim` ghim digest thật
  `sha256:8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024`
  (tra Docker Hub ngày 20/10/2026).
- Học từ c2a (`/opt/claude-c2a/chatgpt2api` @ d879b03, `services/rclone_service.py`):
  ba ràng buộc an toàn (khoá thư mục làm việc bằng `resolve()`; không bao giờ qua
  shell; tệp cấu hình 0600), `--obscure --` khi tạo remote, che khoá khi xem cấu
  hình, đổi mã OAuth Drive cho token. Khác c2a: chạy tiến trình riêng, thư viện
  chuẩn, chuyển tệp nhị phân qua `/tep-len` + `/tep-xuong`.

## Từng bước dựng và chạy

1. **Dựng ảnh** (máy có Docker; thư mục này):
   ```bash
   cd dich-vu/rclone
   docker build -t rclone-bridge:1.0 .
   ```
   Lúc dựng, Dockerfile tự: tải rclone v1.75.2 đúng kiến trúc, kiểm
   `sha256sum -c` (không tin mạng), chuyển sang uid không root (`rclone`, uid 1000).

2. **Chạy** (sinh khoá riêng, KHÔNG khoá mặc định):
   ```bash
   KHOA=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
   docker network create mang-rclone || true

   docker run -d --name rclone-bridge \
     --network mang-rclone \
     --cap-drop ALL --security-opt no-new-privileges:true \
     --memory 256m --pids-limit 64 \
     -p 127.0.0.1:8702:8702 \
     -e RCLONE_KHOA="$KHOA" \
     -v rclone-bridge-data:/data \
     rclone-bridge:1.0
   ```
   - Dịch vụ **chỉ công bố loopback trên host** (`-p 127.0.0.1:8702:8702`);
     container web2api trong cùng mạng `mang-rclone` gọi qua
     `http://rclone-bridge:8702`. Mạng KHÔNG dùng `--internal`: mạng
     `--internal` vừa không công bố được cổng vừa cắt đường ra Internet mà
     rclone bắt buộc phải có để tới backend đám mây (Google, S3…). Đánh đổi:
     khoanh vùng bằng việc chỉ publish loopback + firewall egress trên host
     nếu muốn siết thêm (rclone chỉ cần HTTPS tới backend đã khai +
     `oauth2.googleapis.com` khi dùng `drive_exchange`).
   - Cấu hình rclone trong `/data/rclone/rclone.conf` (0600) là bề mặt nhạy
     cảm nhất — volume này không sao lưu chung, không share.

3. **Kiểm còn sống** (không cần khoá):
   ```bash
   curl http://127.0.0.1:8702/suc-khoe   # → {"ok": true}
   ```

4. **Nối vào web2api**: cài module `rclone` (xem `modules/rclone/README.md`), đặt
   `dich_vu_url` + `khoa_dich_vu` = `$KHOA` ở trên. **Không sửa**
   `Dockerfile`/`docker-compose.yml` gốc của web2api.

## Hợp đồng dịch vụ (tóm tắt)

- `POST /lenh` — thân `{"lenh": "<trong tập đóng>", "tham_so": {...}}`; header
  `Authorization: Bearer <RCLONE_KHOA>`. Tập đóng (16 op): `trang_thai`,
  `remotes`, `config_xem`, `config_ghi`, `them_remote`, `xoa_remote`,
  `kiem_tra`, `drive_start`, `drive_exchange`, `luu_khoa_json`, `liet_ke`,
  `doc_chu`, `tai_ve`, `gui_len`, `xoa`, `chuyen_tep`. Mọi op ngoài tập bị chặn
  **trước** khi gọi nhị phân; lệnh rclone luôn là danh sách đối số (không shell).
- Đường cục bộ qua `resolve()` dưới `/data/workspace`; `config create` luôn
  `--obscure --`; hết giờ 30 s (tra cứu) / 600 s (truyền); đầu ra cắt ở trần.
  Tiến trình rclone con chỉ nhận môi trường tối thiểu (PATH/HOME/LANG) —
  không thừa hưởng `RCLONE_KHOA` hay biến nào khác của dịch vụ.
- Loại remote + tham số remote là **bảng ĐÓNG**: chỉ `drive`, `s3`, `webdav`,
  `onedrive`, `dropbox`, `box`, `mega`, `ftp`, `sftp`, `http` với đúng tập
  tham số của từng loại. Cấm `local`, `sftp` kèm tham số `ssh`, remote chồng
  (`alias`/`union`/`combine`/`hasher`/`crypt`…) và mọi tham số lạ — ở cả
  `them_remote` lẫn `config_ghi` (phân tích INI rồi áp cùng bảng).
- `POST /tep-len` (thân thô ≤ 25 MB): nhận byte lên dịch vụ trước khi
  `gui_len`; `GET /tep-xuong/<id>`: truyền byte đã tải về theo khúc rồi XOÁ
  tệp tạm (mỗi id tải được một lần); `tai_ve` chặn ở trần 25 MB; tệp tạm mồ
  côi quá 10 phút tự dọn.
- `config_xem` luôn trả bản đã che THEO CẤU TRÚC (mặc định che mọi giá trị,
  chỉ siêu dữ liệu an toàn mới hiện); token/R2/S3/Drive không bao giờ vào
  log hay phản hồi.
- `GET /suc-khoe`: mở, không cần khoá, chỉ báo còn sống. Thiếu/sai khoá → `401`
  (không lộ lý do chi tiết); dịch vụ từ chối khởi động nếu `RCLONE_KHOA` yếu
  (< 24 ký tự hoặc quá đơn điệu).

## Kiểm thử (không cần nhị phân thật)

```bash
.venv-web2api/bin/python -m pytest dich-vu/rclone/ -q -p no:cacheprovider
```

`test_server.py` dựng máy chủ thật trên 127.0.0.1 với nhị phân **giả**
(`ho_tro_test/rclone_gia.py`, trỏ qua `RCLONE_BIN`): kiểm tập lệnh đóng, 401,
path traversal/symlink, che khoá, ghi cấu hình hỏng thì giữ bản cũ, `--obscure`,
drive_start, upload/download hai bước. `test_server_sua.py` (NV161-SUA) kiểm
vòng sửa theo soát độc lập: bảng loại/tham số remote đóng, môi trường tối
thiểu cho tiến trình con, che cấu hình theo cấu trúc, trần + dọn tệp tạm,
401 không đọc thân. `test_dockerfile.py` kiểm tĩnh Dockerfile
(ghim digest + SHA-256 từng kiến trúc, uid không root, không `curl|sh`).

## CHƯA kiểm (ghi `null` theo khuôn)

- Chưa chạy rclone thật với bất kỳ backend nào (chỉ nhị phân giả); chưa đo
  RAM/đĩa của container (`tai_nguyen` trong manifest module điền `null`).
- Chưa dựng ảnh Docker thật trong môi trường hộp cát (không có Docker daemon).
- `drive_exchange` chưa kiểm với Google thật (kiểm bằng `urlopen` giả).
