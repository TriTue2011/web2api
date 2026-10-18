# Module home-assistant — Thiết bị nhà qua REST API của Home Assistant

Module đầu tiên cắm vào **chuẩn thiết bị C1** của lõi: đọc trạng thái và điều khiển
có kiểm soát các thực thể Home Assistant (HA) trong mạng nhà.

## Cách hoạt động

- **Liệt kê** (`thuc_the`): `GET /api/states` của HA → mỗi `entity_id` (vd `light.den_tran`)
  thành một thực thể web2api có id **`home_assistant.light.den_tran`** — giữ nguyên
  dấu chấm của HA, chỉ thêm tiền tố `home_assistant.`.
- **Quy ước id (đảo ngược được, ổn định):** bỏ tiền tố `home_assistant.` ở đầu, phần còn
  lại CHÍNH LÀ `entity_id` nguyên văn của HA (kể cả miền có gạch dưới như
  `media_player`: `home_assistant.media_player.tv` → `media_player.tv`). Không đổi
  dấu chấm thành gạch dưới, nên không bao giờ nhập nhằng.
- **Khu vực** (`khu`): lấy bằng `POST /api/template` với `{{ area_name(entity_id) }}`
  theo lô (tối đa 100 entity/lần), có bộ đệm 10 phút; lỗi → để trống.
- **Trạng thái** (`trang_thai`): bộ đệm 20 giây; entity `unavailable`/`unknown` → `hop_le: false`.
- **Điều khiển** (`thuc_hien`): `POST /api/services/<miền>/<dịch vụ>` — miền và dịch vụ
  lấy từ **bảng ánh xạ nội bộ theo hành động** (`_KHA_NANG_MIEN` trong `module.py`),
  không bao giờ ghép từ chuỗi người dùng; `entity_id` phải thuộc danh sách module vừa
  liệt kê; tham số kiểm theo lược đồ trước khi gửi; `ok` là bool thật, lỗi HA không
  lộ nguyên văn.

## Mức rủi ro (theo CẤU TRÚC miền, trong module)

| Miền | Rủi ro |
|---|---|
| `sensor`, `binary_sensor`, miền chỉ đọc/không có hành động | `doc` |
| `light`, `switch`, `fan`, `scene`, `media_player`, `input_boolean`, `climate`, `cover` thường | `ghi` |
| `lock`, `alarm_control_panel`, `water_heater`, `valve`, `cover` có `device_class` garage/gate/door | `nguy_hiem` |

Luật chủ nhà: khoá cửa / bếp / bình nóng lạnh KHÔNG BAO GIỜ tự làm — luôn `nguy_hiem`,
qua hàng chờ phê duyệt của lõi. Miền chạy mã tuỳ ý (`script`, `automation`,
`shell_command`, `rest_command`, `python_script`, `hassio`) **không bao giờ** điều khiển
được qua module này, kể cả khi ghi vào `nhom_duoc_dieu_khien`.

## Bảo mật

- `dia_chi` kiểm bằng ĐÚNG `app/tu_xa.py` (`kiem_url_lan`): cho LAN riêng tư
  (10/8, 172.16/12, 192.168/16); chặn loopback, link-local/metadata đám mây
  (169.254.0.0/16), địa chỉ không định tuyến; tên máy được phân giải và **ghim IP
  đã kiểm chống DNS rebinding** lúc kết nối (`TransportGhimIp` của lõi).
- Token Long-Lived chỉ đi trong header `Authorization`; không vào log/URL/phản hồi/ngoại lệ.
- `/api/states` đọc luồng có **trần cứng 8 MiB** (vượt → bỏ an toàn); tối đa **5000 thực thể**.
- Tên/thuộc tính từ HA là **không tin**: bỏ ký tự điều khiển, cắt độ dài; `thuoc_tinh`
  chỉ giữ `device_class`, `unit_of_measurement` (không chép nguyên `attributes`).

## Cấu hình

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `dia_chi` | Có | URL gốc HA, vd `http://192.168.1.10:8123` |
| `token` | Có | Long-Lived Access Token (Hồ sơ → Bảo mật), lưu riêng, không trả lại |
| `kiem_chung_chi_tls` | Không | `true` (mặc định) kiểm chứng chỉ khi dùng https |
| `nhom_duoc_dieu_khien` | Không | Các miền được ĐIỀU KHIỂN, cách nhau dấu phẩy; mặc định `light,switch,fan,scene,media_player,input_boolean,climate`; miền ngoài danh sách chỉ đọc |

## Giới hạn

- Chưa kiểm với Home Assistant thật (chỉ máy chủ giả).
- Sự kiện thời gian thực (`su_kien`) chưa làm — chuẩn cho phép tuỳ chọn.
- Chưa đo RAM/đĩa thật.
