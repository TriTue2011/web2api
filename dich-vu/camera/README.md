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
`127.0.0.1:8714` — chỉ công bố trong mạng Docker nội bộ với web2api. Khi
publish cổng ra khỏi container (`docker run -p ...`), phải đặt thêm
`-e CAM_HOST=0.0.0.0`: nghe 127.0.0.1 TRONG container thì cổng publish
không tới được.

## Hợp đồng với module (NV173B)

- Module đăng ký bảng camera một lần bằng `POST /dang-ky`
  `{danh_sach: [{id, name, kind, ...}]}` (cần khoá). Dịch vụ kiểm TỪNG bản
  ghi trước khi nhận (`chup.kiem_ban_ghi`): kind chỉ `go2rtc`/`rtsp`,
  scheme đóng (`base` http/https, `url` rtsp), host phải phân giải ra địa
  chỉ mạng RIÊNG — loopback của chính container, link-local/metadata và IP
  công khai đều bị chặn — và GHIM IP đã kiểm vào bản ghi.
- Mọi đường còn lại (`/chup`, `/phat`, `/bo_dam/mo`) chỉ nhận `cam_id` và
  tra bảng đã đăng ký — dịch vụ KHÔNG nhận URL theo từng lời gọi nữa
  (chống SSRF + `ffmpeg -i` scheme tuỳ ý). ffmpeg RTSP luôn chạy với
  `-protocol_whitelist rtsp,tcp,udp,rtp`.
- Khoá Bearer được kiểm TRƯỚC khi đọc thân yêu cầu; khoá sai chỉ bị rút
  tối đa 1 KB rồi đóng kết nối.
- URL RTSP (chứa mật khẩu) đi vào argv của ffmpeg bên TRONG container
  riêng không-root này — điều kiện chấp nhận theo quyết định điều phối:
  chỉ chạy ở container tách riêng như ở đây, không chạy trên máy chủ
  web2api chính; đường ưu tiên vẫn là go2rtc nội bộ (module chụp trực
  tiếp qua HTTP khi không cần ffmpeg).

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
| `CAM_BO_DAM_TOI_DA_PHIEN` | 4 | Trần số phiên bộ đàm mở đồng thời |
| `CAM_BO_DAM_HET_HAN_GIAY` | 60 | Phiên bộ đàm im quá ngần này giây thì hết hạn |
| `CAM_BO_DAM_TONG_GIAY` | 300 | Trần tổng thời lượng nói của một phiên bộ đàm |
| `CAM_TEST_CHO_PHEP_LOOPBACK` | — | CHỈ cho test: `1` thì bản ghi đăng ký được dùng địa chỉ loopback (go2rtc giả trên máy test). Tuyệt đối không bật khi chạy thật |
| `CAM_HIK_LIB` | — | Thư mục `lib` của HCNetSDK hãng (EZVIZ/Hik) nếu dùng |
| `CAM_HIK_SCRIPT` | — | Đường dẫn `hik_noi.py` nếu dùng kênh Hik |

Chưa dựng thử trong môi trường soạn bài (không có Docker daemon).
