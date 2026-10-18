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

## Khớp thiết bị nhanh không cần LLM (`khop_thiet_bi`)

Ngoài điều khiển trực tiếp bằng `entity_id`, module cung cấp công cụ khớp câu nói người dùng
với thiết bị nhanh thuần Python, không tốn tài nguyên GPU hay lượt LLM:

- **Xếp hạng đa tín hiệu:**
  - Tương đồng chuỗi LCS ký tự và so khớp token không thứ tự, có phạt chênh lệch độ dài.
  - Tương đồng Jaccard 3-gram ký tự (bền với lỗi gõ, sai chính tả nhẹ).
  - BM25 tính trên chính tập ứng viên thực thể (từ hiếm mang tính nhận diện riêng như "ngủ", "chùm" nặng điểm hơn từ chung như "đèn", "phòng").
  - Tương hợp hành động (`bat`, `tat`, `chuyen`, `mo`, `dong`): câu "tắt đèn" không bao giờ khớp thiết bị khi yêu cầu hành động "bật".
- **Chuẩn hoá:** NFKC → chuyển chữ thường → bỏ dấu tiếng Việt (kể cả `đ` → `d`) → bỏ dấu câu; trộn câu không dấu và tên thiết bị có dấu vẫn khớp chuẩn xác.
- **Cổng an toàn căn từ:** Mỗi từ có nghĩa trong câu phải có từ tương ứng ở thực thể ứng viên (ngăn chặn chọn bừa thiết bị khi câu nói chứa từ vựng hoàn toàn không liên quan).
- **Biên mơ hồ an toàn:** Nếu hai ứng viên điểm cao nhất sát nhau (chênh lệch < 0.08), hệ thống đánh dấu `mo_ho: true` và không tự chọn hộ.
- **Từ khoá gọi (`tu_khoa_goi`):** Cấu hình các từ đánh thức/từ khoá gọi ở đầu câu (phân cách bằng dấu phẩy). Khi câu bắt đầu bằng từ khoá này, hệ thống bỏ qua khớp nhanh và chuyển thẳng sang LLM xử lý kèm câu đã cắt từ khoá.
- **Kiểm tra khả thi trước điều khiển (`kiem_kha_thi`):** Kiểm tra trạng thái và quyền điều khiển của thực thể trước khi thực thi. Nếu ứng viên top 1 không khả thi (miền cấm, thiết bị unavailable), tự động bỏ qua và phục hồi sang ứng viên kế tiếp (tối đa 1 lần).
- **Chẩn đoán chi tiết (`giai_thich_khop`):** Bóc tách điểm số từng tín hiệu (LCS, 3-gram, BM25, intent affinity, hot words) hỗ trợ gỡ lỗi và tinh chỉnh.

## Nối HA Assist vào web2api

Home Assistant Assist có thể dùng web2api làm backend AI cục bộ hoặc cầu nối điều khiển thiết bị:

1. **Chuẩn OpenAI tương thích:** web2api cung cấp endpoint `/v1/chat/completions` chuẩn OpenAI. Trong HA, cấu hình tích hợp OpenAI Conversation hoặc custom integration (ví dụ `hass_local_openai_llm` — *lưu ý: luôn kiểm tra LICENSE và mã nguồn của custom integration trước khi cài*), đặt URL là `http://<dia-chi-web2api>:8000/v1`.
2. **Khoá khách quyền hẹp (Bảo mật):** Tạo khoá API khách riêng cho HA Assist trong web2api, chỉ cấp phạm vi tối thiểu cần thiết (`thiet_bi:doc`, `thiet_bi:ghi`). Tuyệt đối không dùng khoá quản trị (`WEB2API_ADMIN_KEY`).
3. **Khi nào dùng khớp nhanh thay LLM:**
   - Dùng **khớp nhanh (`khop_thiet_bi`)**: Với các câu lệnh khẩu lệnh trực tiếp trong nhà ("bật đèn bếp", "tắt quạt ngủ"). Thời gian phản hồi tính bằng mili-giây (< 100ms), không phụ thuộc mạng ngoài, không tốn quota API.
   - Dùng **LLM**: Khi câu nói mang tính đối thoại, giải thích, tra cứu thông tin tổng hợp hoặc suy luận nhiều bước.
4. **An toàn rủi ro cao (Chuẩn C1):** Dù đi qua LLM hay khớp nhanh, mọi hành động tác động đến thiết bị thuộc nhóm `nguy_hiem` (khoá cửa `lock`, van gas/nước `valve`, bình nước nóng `water_heater`, cửa cuốn/cổng `cover`) **bắt buộc phải qua hàng chờ phê duyệt** của lõi hoặc yêu cầu xác nhận rõ ràng từ chủ nhà, không bao giờ được tự động thực thi.

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
| `dia_chi` | Có | URL gốc HA, vd `http://192.0.2.10:8123` |
| `token` | Có | Long-Lived Access Token (Hồ sơ → Bảo mật), lưu riêng, không trả lại |
| `kiem_chung_chi_tls` | Không | `true` (mặc định) kiểm chứng chỉ khi dùng https |
| `nhom_duoc_dieu_khien` | Không | Các miền được ĐIỀU KHIỂN, cách nhau dấu phẩy; mặc định `light,switch,fan,scene,media_player,input_boolean,climate`; miền ngoài danh sách chỉ đọc |
| `tu_khoa_goi` | Không | Từ khoá gọi (hot words) ở đầu câu chuyển sang LLM, phân cách bằng dấu phẩy |

## Giới hạn

- Chưa kiểm với Home Assistant thật (chỉ máy chủ giả).
- Sự kiện thời gian thực (`su_kien`) chưa làm — chuẩn cho phép tuỳ chọn.
- Chưa đo RAM/đĩa thật.
