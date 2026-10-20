# Dịch vụ youtube-phat — máy phát YouTube/Zing cho web2api (NV153)

Port từ máy phát YouTube nằm trong c2a (`chatgpt2api/api/youtube_phat.py` +
`services/youtube_phat/`, commit d879b03 — CHỈ ĐỌC nguồn, không sửa c2a),
thành dịch vụ phụ độc lập cho web2api. Home Assistant (tích hợp
`tritue_youtube_player`) trỏ sang dịch vụ này thay vì `/yt` của c2a.

## Vì sao có dịch vụ này

Đo thật trên máy phát c2a ngày 19/10/2026 (bài 95 phút, `Content-Length`
hứa 92.178.113 byte, đọc chậm như loa):

| Tốc độ đọc | Kết quả |
|---|---|
| 17 KB/s | Luồng KẾT THÚC SỚM lúc 467 s (7.929.856 B), lặp lại lúc 486 s (8.257.536 B) — êm, KHÔNG log |
| 60 KB/s | Cắt lúc 278 s (16.646.144 B) |
| 200 KB/s | Đủ 92.178.113 B, không cắt |

Điểm cắt cách ranh giới khúc 4 MiB đúng 131.072 hoặc 458.752 byte.
Thực tế tối 19/10: loa `d50s` phát bài dài rồi về idle sau ~451 s trong khi
thực thể YouTube vẫn báo `playing` — phiên «sống» mà loa đã im, c2a không
ghi dòng log nào.

Nguyên nhân trong c2a (`api/youtube_phat.py::_noi_cac_khuc`): `except
(OSError, HTTPError): return` im lặng, và các khúc sau khúc đầu KHÔNG giải
lại link khi 403/410 (khúc đầu thì có). Dịch vụ này viết lại rơ-le luồng
(`ro_le.py`): nối lại tối đa 5 lần (chờ 0,5 / 1 / 2 / 2 / 2 s) từ ĐÚNG byte
đã gửi, 403/404/410 thì quên link cũ và giải lại, kiểm `Content-Range` của
bản nối (phải cùng tổng cỡ, bắt đầu đúng offset — sai thì tính 1 lần thất
bại, không gửi byte nào), hết lần thì NGẮT (không lấp rác) + log JSON.

## Chạy

```bash
cd dich-vu/youtube-phat
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt
YT_DATA_DIR=/tmp/yt-data YT_HOST=127.0.0.1 YT_PORT=8713 python3 server.py
```

Docker (khuyến nghị khi chạy thật):

```bash
docker compose up -d --build
docker compose exec youtube-phat cat /data/youtube_phat/integration_token
```

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `YT_DATA_DIR` | `/data` | Thư mục dữ liệu: `youtube_phat/` chứa `playlists.json`, `history.json`, `integration_token` (0600), `hmac_key` (0600) |
| `YT_HOST` | `127.0.0.1` | Địa chỉ nghe. Mặc định CHỈ nội bộ (luật cứng số 5) |
| `YT_PORT` | `8713` | Cổng nghe |
| `YT_PUBLISH_IP` | (trống) | IP LAN của máy này — URL ký cho loa dùng `http://<ip>:<port>`; không đặt thì lấy theo `Host` của request |

## Bật mạng LAN (cho Home Assistant và loa)

Cả Home Assistant và loa đều phải tới được cổng này:

```yaml
# docker-compose.yml — bỏ comment hai dòng environment:
YT_HOST: "0.0.0.0"            # hoặc IP LAN cụ thể, KHÔNG để mặc định khi cần LAN
YT_PUBLISH_IP: "192.168.1.50" # IP LAN thật của máy này
```

Rủi ro: ai trong LAN cũng gọi được API nếu có token — giữ `integration_token`
như mật khẩu. Không dựa UPnP, không mở ra Internet.

## Trỏ Home Assistant sang

Trong mục tích hợp `tritue_youtube_player`: đổi URL gốc thành
`http://<máy>:8713` (KHÔNG còn `/yt`), dán token mới lấy ở
`/data/youtube_phat/integration_token` (tệp 0600).

Chuyển dữ liệu từ c2a: chép các tệp trong `<data c2a>/youtube_phat/`
(`playlists.json`, `history.json`) vào volume `/data/youtube_phat/` rồi
khởi động lại dịch vụ. Không chép `integration_token` cũ nếu muốn token mới
(xoá tệp để dịch vụ tự sinh).

## Thử bằng curl

```bash
K=... # integration_token
# Tìm kiếm
curl -s -H "Authorization: Bearer $K" \
  "http://127.0.0.1:8713/api/integration/search?q=nhac&source=youtube&limit=5"
# Lấy URL luồng
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"source":"youtube","target":"<ma_video_11_ky_tu>"}' \
  http://127.0.0.1:8713/api/integration/stream
# Đọc chậm như loa (20 KB/s) để kiểm rơ-le chịu lỗi:
curl --limit-rate 20k -o /dev/null "<stream_url>"
```

## Dừng là dừng hẳn

1. **Phiên không sống lại sau restart**: phiên đang phát chỉ ở RAM
   (`session.py` không ghi đĩa). Restart xong `GET /api/integration/status`
   không còn phiên nào; không tự phát lại gì.
   Kiểm: phát → restart → `status` báo không phiên.
2. **SIGTERM đóng ngay mọi luồng**: rơ-le đang đẩy cho loa bị cắt ngay
   (không đẩy nốt bộ đệm), đóng kết nối lên nguồn, thoát trong vài giây.
   Kiểm: mở luồng → gửi SIGTERM → phía loa thấy kết thúc, không còn request
   lên nguồn.
3. **Tắt/kẹt thì không trả trạng thái cũ**: `/status` đọc trạng thái thật
   lúc hỏi; dịch vụ kẹt thì không trả lời hoặc trả lỗi — tích hợp HA dựa
   vào đó để biết «máy phát ngừng trả lời» (quá 15 s).
4. **Chỉ nối lại khi NGUỒN cắt**: loa đóng kết nối giữa chừng → dừng ngay,
   không thêm request lên nguồn, không rò kết nối/task nền; đang dừng thì
   không nối lại.
5. **Hết 5 lần thì ngắt**: không «phát tiếp bằng đường khác». Tự chuyển bài
   là việc của tích hợp Home Assistant — dịch vụ không tự phát bài kế.

## API (hợp đồng khớp c2a)

Tiền tố gốc `/api/integration/*` (không `/yt`). Tất cả cần
`Authorization: Bearer <integration_token>` (kể cả `/health` — c2a làm vậy);
sai/thiếu → 401 `{"error": "invalid_auth"}`. Mọi phản hồi JSON kèm
`Cache-Control: no-store`.

| Đường | Giới hạn thân | Ghi chú |
|---|---|---|
| `GET /api/integration/health` | — | `{"success": true, "status": "ok", "capabilities": [...]}` |
| `GET /api/integration/search?q=&source=&limit=` | — | source ∈ youtube/zing/facebook; limit 1–30 |
| `GET /api/integration/status` | — | trạng thái thật lúc hỏi |
| `GET /api/integration/history` | — | |
| `POST /api/integration/stop` | 256 B | thân rỗng = dừng tất cả |
| `POST /api/integration/session` | 8192 B | |
| `GET /api/integration/playlists` | — | |
| `POST /api/integration/playlists` | 400_000 B | action list/create/rename/delete/add/remove/move/export/import; mã chia sẻ `TTPL1.` |
| `POST /api/integration/session/outputs` | 4096 B | |
| `POST /api/integration/stream` | 4096 B | trả `stream_url` ký HMAC (hạn 1 giờ), `expires_in: 3600` |
| `POST /api/integration/play` | 4096 B | |
| `GET /api/stream/{token}` | — | KHÔNG cần Bearer — token ký HMAC trên URL (loa không gửi được header) |
| `HEAD /api/stream/{token}` | — | như GET nhưng chỉ header; token `.m3u8` không strip đuôi (giữ đúng c2a) |

Thân rỗng/không phải dict/quá cỡ → 400 `{"error": "invalid_request"}`.

## Gói bên thứ ba

| Gói | Phiên bản | Giấy phép | Đã đọc mã? | Dùng để |
|---|---|---|---|---|
| `fastapi` | 0.142.4 | MIT | Rồi (lõi dùng) | khung HTTP |
| `uvicorn` | 0.54.0 | BSD-3 | Rồi (lõi dùng) | máy chủ ASGI |
| `yt-dlp` | 2026.8.19 | Unlicense | Chưa đọc hết (chỉ API công khai; ~mã nguồn lớn) | giải luồng YouTube |
| `yt-dlp-ejs` | 0.8.0 | MIT | Chưa | script JS giải thử thách YouTube |
| `deno` | 2.9.7 | MIT | Chưa (nhị phân) | trình chạy JS cho yt-dlp |

Lý do trong thông điệp commit. `deno` là wheel riêng linux/x86_64 — dựng cho
arm64 thì tải lại wheel arm64 và thay hash trong `requirements.txt`.

## Kích thước ảnh

Chưa đo (môi trường làm việc không có Docker daemon).

## CHƯA kiểm

- yt-dlp THẬT (test dùng yt-dlp giả), YouTube thật, loa thật, iOS (`.m3u8`).
- Dựng ảnh Docker thật; đo RAM/đĩa thật.
- Tải mô hình/khoá Zing thật (`zing_keys.json` do chủ máy đặt tay).
