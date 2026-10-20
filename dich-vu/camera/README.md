# Dịch vụ phụ Camera (NV167)

Container phụ chạy `server.py` (chỉ thư viện chuẩn): chụp ảnh camera (go2rtc qua
HTTP, RTSP qua ffmpeg) và phát tiếng ra loa camera (giao thức NetSDK cổng 37777,
HTTP 8086, HCNetSDK cổng 8000 — mã chép từ c2a, xem `kenh.py`).

## Chạy

```bash
# Sinh khoá riêng (ghi lại, dùng cho CAM_KHOA và trường dich_vu_khoa của module):
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

docker build -t camera-dv dich-vu/camera
docker run -d --name camera-dv --restart unless-stopped \
  --network <mang-noi-bo> \
  -e CAM_KHOA='<khoá đã sinh>' \
  camera-dv
```

Khoá phải ≥ 24 ký tự; dịch vụ từ chối khởi động nếu yếu. Mặc định nghe
`127.0.0.1:8714` — chỉ công bố trong mạng Docker nội bộ với web2api.

## Kiểm tra

```bash
curl -s http://127.0.0.1:8714/suc-khoe            # {"ok": true} (không cần khoá)
```

Ảnh container có sẵn `ffmpeg` (bản tĩnh BtbN, ghim SHA-256 trong Dockerfile).
Máy chạy **test cục bộ** của module `cameranha` cũng cần `ffmpeg` trên PATH để
chạy đủ bộ (sinh JPEG thật); thiếu thì các test cần ffmpeg tự bỏ qua.

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `CAM_KHOA` | (bắt buộc) | Khoá Bearer <redacted> |
| `CAM_HOST` / `CAM_CONG` | 127.0.0.1 / 8714 | Địa chỉ nghe |
| `CAM_PHAT_TOI_DA_GIAY` | 30 | Trần thời lượng một lần phát (giây) |
| `CAM_PHAT_GIAN_CACH_GIAY` | 2 | Giãn cách tối thiểu giữa hai lần phát |
| `CAM_HIK_LIB` | — | Thư mục `lib` của HCNetSDK hãng (EZVIZ/Hik) nếu dùng |
| `CAM_HIK_SCRIPT` | — | Đường dẫn `hik_noi.py` nếu dùng kênh Hik |

Chưa dựng thử trong môi trường soạn bài (không có Docker daemon).
