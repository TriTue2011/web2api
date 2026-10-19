# Dịch vụ van-phong-office — OfficeCLI trong container phụ (NV138)

Một dịch vụ HTTP nhỏ chạy **riêng** cạnh web2api: nhận lệnh thao tác tài liệu
văn phòng (docx/xlsx/pptx) qua `POST /lenh`, gọi nhị phân OfficeCLI trong hộp
cát rồi trả kết quả. web2api **không** nhúng nhị phân vào ảnh lõi (lõi 256 MB —
«module nặng ở container riêng», quyết định chủ máy 18/10).

## Đã đọc về OfficeCLI (ghi theo yêu cầu spec)

- Nguồn: `github.com/iOfficeAI/OfficeCLI` — binary tự chứa .NET, giấy phép
  **Apache-2.0** (theo spec NV138).
- Bản phát hành **v1.0.140**: tồn tại trên GitHub (kiểm ngày 19/10/2026,
  `https://github.com/iOfficeAI/OfficeCLI/releases/tag/v1.0.140`, commit
  `e7916a2`, phát hành 22/07). Trang phát hành **không nêu checksum** ⇒ tự tải
  nhị phân về và tính SHA-256, ghim vào Dockerfile (xem ghi chú trong
  `Dockerfile`: bản x64 đã tải về và tính lại ngày 18/10/2026, KHỚP).
- Học từ c2a (`TriTue2011/chatgpt2api`, đọc ngày 18/10/2026):
  `services/officecli.py` (tập lệnh đóng, hết giờ 60/180 s, đầu ra ≤ 80.000 ký
  tự, thư mục làm việc khoá `DATA_DIR/office`) và `services/office_bo_sung.py`.
  Chỉ học ý — **khác** c2a: không gọi OfficeCLI trong tiến trình web2api, không
  dùng SDK (SDK có thể tự tải CLI), mà chạy qua dịch vụ HTTP riêng này.

## Từng bước dựng và chạy

1. **Dựng ảnh** (máy có Docker; thư mục này):
   ```bash
   cd dich-vu/van-phong-office
   docker build -t van-phong-office:1.0 .
   ```
   Lúc dựng, Dockerfile tự: tải OfficeCLI v1.0.140 đúng kiến trúc
   (`officecli-linux-x64` / `officecli-linux-arm64`), kiểm `sha256sum -c`
   (không tin mạng), chạy `officecli config autoUpdate false` (tắt kiểm tra cập
   nhật nền — **bắt buộc**, vì OfficeCLI tự kiểm cập nhật trong nền), chuyển sang
   uid không root (`office`, uid 1000).

2. **Chạy** (sinh khoá riêng, KHÔNG khoá mặc định):
   Dịch vụ lắng nghe `0.0.0.0:8701` bên trong container. Để bảo mật mạng:
   - Dùng mạng Docker nội bộ (`internal: true`, không định tuyến ra Internet) để nối với container web2api.
   - Thêm giới hạn tài nguyên (`--memory 512m --pids-limit 64`) và cô lập quyền (`--cap-drop ALL --security-opt no-new-privileges:true`).
   - Chỉ công bố ra `127.0.0.1:8701` trên máy chủ host nếu cần truy cập từ host loopback.

   ```bash
   KHOA=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
   # Tạo mạng nội bộ không có gateway ra ngoài
   docker network create --internal mang-office-noi-bo || true

   docker run -d --name van-phong-office \
     --network mang-office-noi-bo \
     --cap-drop ALL --security-opt no-new-privileges:true \
     --memory 512m --pids-limit 64 \
     -p 127.0.0.1:8701:8701 \
     -e OFFICE_KHOA="$KHOA" \
     -v van-phong-office-data:/data \
     van-phong-office:1.0
   ```
   - Không có mạng ra ngoài Internet (mạng Docker nội bộ + kiểm tra cập nhật nền đã tắt từ lúc dựng + `OFFICECLI_SKIP_UPDATE=1` lúc chạy).
   - Chỉ công bố cổng trên `127.0.0.1` của host (không bind 0.0.0.0 ngoài host); container web2api trong cùng mạng `mang-office-noi-bo` có thể gọi qua tên dịch vụ `http://van-phong-office:8701`.
   - `/data` là volume riêng (thư mục làm việc); `/app/server.py` chỉ đọc.

3. **Kiểm còn sống** (không cần khoá):
   ```bash
   curl http://127.0.0.1:8701/suc-khoe   # → {"ok": true}
   ```

4. **Nối vào web2api**: cài module `van-phong` (xem `modules/van-phong/README.md`),
   bật `office_bat`, đặt `dich_vu_url` + `khoa_dich_vu` = `$KHOA` ở trên.
   **Không sửa** `Dockerfile`/`docker-compose.yml` gốc của web2api — dịch vụ này
   có compose/Dockerfile riêng.

## Hợp đồng dịch vụ (tóm tắt)

- `POST /lenh` — thân `{"lenh": "<trong tập đóng>", "doi_so": [...]}`; header
  `Authorization: Bearer <OFFICE_KHOA>`. Tập đóng (19 lệnh):
  `create view get query set add remove move swap validate batch merge dump open
  close save help import refresh` — lệnh `mcp` và mọi lệnh ngoài tập bị chặn
  **trước** khi gọi nhị phân (không chạm tới tiến trình).
- Mọi đối số đường dẫn qua `resolve()` dưới `/data`; từ chối ký tự điều khiển,
  `..`, symlink ra ngoài; không bao giờ qua shell (danh sách đối số).
- Hết giờ 60 s (`batch`/`merge`/`import`: 180 s); đầu ra ≤ 80.000 ký tự (cắt).
- `POST /tai-len` (multipart, trường `tep`): ≤ 25 MB, kiểm magic bytes zip
  OOXML (`PK\x03\x04`) + đuôi `.docx`/`.xlsx`/`.pptx`; tên tệp do hệ thống sinh
  (id 16 hex). `GET /tai-xuong/<id>`: trả đúng nội dung.
- `GET /suc-khoe`: mở, không cần khoá, chỉ báo còn sống.
- Thiếu/sai khoá → `401` (không lộ lý do chi tiết); dịch vụ từ chối khởi động
  nếu `OFFICE_KHOA` yếu (< 24 ký tự hoặc quá đơn điệu) hoặc thiếu
  `OFFICECLI_SKIP_UPDATE=1`.

## Kiểm thử (không cần nhị phân thật)

```bash
.venv-web2api/bin/python -m pytest dich-vu/van-phong-office/ -q -p no:cacheprovider
```

`test_server.py` dựng máy chủ thật trên 127.0.0.1 với nhị phân **giả**
(`ho_tro_test/officecli_gia.py`, trỏ qua `OFFICE_BIN`): kiểm tập lệnh đóng
(≥ 15 lệnh ngoài tập/lách), path traversal/symlink, hết giờ, đầu ra quá lớn,
khoá sai → 401, tải lên/tải xuống. `test_dockerfile.py` kiểm tĩnh Dockerfile
(ghim SHA-256 từng kiến trúc + `sha256sum -c`, uid không root,
`OFFICECLI_SKIP_UPDATE=1`, `officecli config autoUpdate false`, COPY đủ mô-đun,
không `curl|sh`, không sửa Dockerfile/compose gốc).

## CHƯA kiểm (ghi `null` theo khuôn)

- Chưa chạy OfficeCLI thật (chỉ nhị phân giả); chưa đo RAM/đĩa của container
  (`tai_nguyen` trong manifest module điền `null`).
- Chưa dựng ảnh Docker thật trong môi trường hộp cát (không có Docker daemon);
  chưa ghim digest ảnh nền `python:3.13-slim` (ghi chú trong Dockerfile — lấy
  bằng `docker buildx imagetools inspect` khi phát hành, không bịa digest).
- SHA-256 trong Dockerfile do người làm NV138 tự tính từ tệp tải về
  (ghi trong Dockerfile); người dựng nên tải lại và đối chiếu khi build.
