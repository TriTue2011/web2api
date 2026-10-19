# Module `mqtt` — Thiết bị nhà qua MQTT, không cần Home Assistant

Đường thứ hai biết nhà (NV151, port từ c2a `services/mqtt_nha.py` @ `d879b03`):
nối máy chủ MQTT trong mạng nhà, tự khám phá thiết bị qua bản tự khai báo
Home Assistant discovery (Zigbee2MQTT, Tasmota…) + nhận dạng Frigate, trả thành
thực thể chuẩn C1 (`mqtt.<tên>`).

## Cài đặt (3 bước)

1. Hệ thống → Module → tìm **`mqtt`** → bấm **Cài đặt**.
2. Mở cấu hình module, điền:
   - **Địa chỉ máy chủ MQTT**: dạng `máy[:cổng]`, vd `192.0.2.10:1883`
     (cổng mặc định 1883). Địa chỉ đi qua bộ kiểm SSRF của lõi
     (`app/tu_xa.py`): cho mạng LAN riêng tư, chặn loopback/link-local/
     metadata đám mây; tên máy được phân giải trước khi nối.
   - **Tài khoản / Mật khẩu** (nếu máy chủ yêu cầu). Mật khẩu là trường
     `bi_mat`: lưu riêng trong kho bí mật của lõi (quyền 0600), **không bao
     giờ** vào log, phản hồi API, sức khoẻ hay `modules.json`.
3. Bấm nút **«Kiểm tra»** (nút `cai()` của module): module nối THẬT tới máy chủ,
   báo số nhánh chủ đề thấy được. Nối được mà không tin nào về thường là quyền
   tài khoản bị giới hạn — cần sửa quyền trên máy chủ MQTT.

## Đọc / điều khiển

- Mặc định module **chỉ đọc**: GHI (publish lệnh) **TẮT** cho tới khi bật ô
  **«Cho phép điều khiển»** trong cấu hình.
- Loại **nguy hiểm** (khoá cửa, báo động, bình nóng lạnh, van, còi — phân loại
  bằng **cấu trúc** component trong bản tự khai báo, không bằng chữ trong tên)
  **KHÔNG BAO GIỜ** điều khiển được qua module này, kể cả khi đã bật ô trên.
- Chủ đề đích của lệnh chỉ lấy từ bản tự khai báo đã nạp — không ghép từ chuỗi
  người gọi đưa vào.

## Khám phá (giữ nguyên bài học EMQX từ c2a)

- Dò nhánh gốc bằng `+` nhiều độ sâu (6 tầng), **KHÔNG BAO GIỜ** đăng ký `#`
  (EMQX và máy chủ đặt `no_match = deny` từ chối `#` và `$SYS/#` một cách
  im lặng — đăng ký `#` sẽ mất sạch dữ liệu mà tưởng máy chủ hỏng).
- Dữ liệu từ máy chủ MQTT là **không tin**: payload > 64 KiB bị bỏ, giá trị
  cắt ở 4096 ký tự, chủ đề dài > 1024 ký tự bị bỏ, tối đa 5000 chủ đề trong RAM.
- Mất kết nối ≠ «0 thiết bị»: sức khoẻ báo «chưa kết nối được: \<lý do\>»,
  giữ dữ liệu cũ kèm dấu thời gian.

## Kích thước

- Mã: ~1000 dòng Python (`mqtt_lop.py` ~680 + `module.py` ~330), ~86 KB trên đĩa.
- Phụ thuộc duy nhất: **`paho-mqtt==2.1.0`** (mã bên thứ ba).
  Khai ở `yeu_cau_goi` của manifest — **không** nằm trong `requirements.txt`
  của lõi; phải có sẵn trong container (lõi từ chối nạp module khi thiếu,
  báo rõ tên gói). Ghim `2.1.0` vì mã dùng API callback `VERSION2` của
  paho-mqtt ≥ 2.0 (bản 1.x không có).
- RAM thêm: chưa đo (sổ chủ đề tối đa 5000 mục).

## CHƯA kiểm

- **Chưa kiểm với máy chủ MQTT thật** (Mosquitto/EMQX/Zigbee2MQTT thật):
  mới kiểm với máy chủ MQTT giả cục bộ (socket thuần, đủ MQTT 3.1.1 cho paho).
- Chưa đo RAM/đĩa thật, chưa dựng ảnh Docker thật có paho-mqtt.
