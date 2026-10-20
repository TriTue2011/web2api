# Module `tuya` — thiết bị Tuya (đám mây + LAN)

Port từ c2a (`services/tuya_nha.py`, `services/tuya_local.py`, commit d879b03 —
c2a CHỈ đọc, không sửa): đường đám mây giữ nguyên công thức ký
HMAC-SHA256 của c2a; đường LAN TỰ VIẾT bằng thư viện chuẩn + `cryptography`
(c2a dùng `tinytuya`, thư viện Tuya bên thứ ba — không port theo luật kho).

## Hai đường

- **Đám mây**: Tuya Open API. Chỉ gọi đúng 6 miền Tuya (kiểm cấu trúc trong
  `dam_may.kiem_mien_tuya`), qua `TransportGhimIp` của lõi (ghim IP đã kiểm,
  chống DNS rebinding). Token 7200s, tự xin lại trước 60s hết hạn.
- **LAN**: thiết bị cắm nguồn trong mạng nhà mở TCP 6668. Giao thức khung
  `55AA` tự viết (`lan.py`): AES-128-ECB + CRC32. Chỉ nối tới IP riêng khai
  tường minh; khoá `local_key` chỉ giữ trong RAM.

## Luật nhà (cấu trúc, không theo tên)

- Điều khiển mặc định TẮT — chỉ đọc. Bật `cho_phep_dieu_khien = "bat"` mới
  điều khiển được ngành an toàn nhỏ (đèn, công tắc, ổ cắm, quạt, rèm…).
- Khoá cửa / ổ khoá / hộp an toàn / bếp / bình nóng lạnh (theo **mã ngành**
  Tuya: videolock, jtmspro, ms, mk, bxx, dcl, kx, rs) **KHÔNG BAO GIỜ**
  điều khiển được — thực thể không có `tinh_nang` nào; `thuc_hien()` kiểm
  lại lần nữa.
- Thực thể trạng thái: đám mây ưu tiên thiết bị `is_online`; LAN ưu tiên
  kết quả `hoi` thật, lỗi giải mã = local_key đã đổi (bài học đo thật của
  c2a ngày 10/09/2026) — báo rõ, không sập.

## Lấy khoá ở đâu

- `access_id` / `access_secret`: iot.tuya.com → đăng nhập → Cloud →
  Development → chọn dự án → Overview → Access ID/Client ID + Access Secret.
- Vùng (`vung`): vùng của **tài khoản** lúc đăng ký app Tuya, không phải nơi
  ở. Sai vùng → mọi lời gọi báo «cross-region».
- `local_key`: module tự lấy qua đường đám mây (`GET /v1.0/devices/{id}`)
  khi đã khai Access ID/Secret; hoặc chủ máy tự điền đúng thứ tự.
- Mã thiết bị: iot.tuya.com → Cloud → Devices → cột Device ID.

## Thiết bị pin

Khoá cửa pin, gateway Zigbee… KHÔNG mở cổng 6668 — dò LAN ra rỗng là trạng
thái bình thường của nhà (đo thật của c2a), không phải lỗi.
