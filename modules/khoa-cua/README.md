# khoa-cua — khoá cửa thông minh (NV169)

Đọc trạng thái khoá, xem nhật ký mở/đóng cửa và học tên người dùng của khoá
Tuya (qua Tuya IoT Open API / đám mây) theo chuẩn thiết bị C1. Nguồn gốc port:
c2a `services/khoa_cua_nha.py` (commit `d879b03`) — mã lệnh mở/đóng cửa KHÔNG
đoán: chủ máy khai rõ trong cấu hình (`lenh_mo` / `lenh_dong`).

## Hàng rào an toàn (bắt buộc, không tắt được bằng tuỳ chọn thường)

- Hành động **mở khoá LUÔN «nguy_hiem» theo cấu trúc**: cổng rủi ro của lõi
  bắt quản trị + `xac_nhan: true`, nếu không bị từ chối (HTTP 409). Người
  dùng khoá khách / mô hình / kênh vào chỉ đi vào hàng chờ phê duyệt, **không
  bao giờ chạy trực tiếp**.
- Ngoài cổng của lõi, module bắt **cửa sổ xác nhận lại (step-up) riêng**:
  quản trị gọi hành động «chuan_bi_mo» → nhận **mã xác nhận dùng một lần,
  gắn đúng quản trị đó, hết hạn sau 120 giây**. «mo_khoa» thiếu mã / hết hạn
  / của quản trị khác / mã cũ gửi lại (chống phát lại) → từ chối. Hai lệnh mở
  đồng thời cùng một mã: chỉ một lệnh chạy.
- Vì mã xác nhận gắn quản trị, đường hàng chờ phê duyệt của lõi **không dùng
  được cho mo_khoa** (lệnh được duyệt không kèm mã xác nhận của người duyệt).
  Quản trị mở khoá bằng đường trực tiếp: `chuan_bi_mo` → `mo_khoa` kèm
  `xac_nhan: true`.
- Mọi lần chuẩn bị mở / mở / đóng (kể cả bị từ chối hoặc lỗi thiết bị) đều
  ghi vào **nhật ký bất biến** `nhat-ky-bat-bien.jsonl` (quyền 0600, chỉ ghi
  thêm, module không có hành động nào xoá nó): **ai, từ đâu, lúc nào**.
- Điều khiển (mở/đóng) **mặc định TẮT** — chỉ khi cấu hình
  `cho_phep_dieu_khien=bat` mới có; tắt thì module chỉ đọc. **Không có** tính
  năng tự mở khi về nhà.
- **Tên người học từ mã vân tay là dữ liệu cá nhân**: lưu `so-ten.json`
  (quyền 0600), không vào log/sức khoẻ/phản hồi khác, quản trị xoá được qua
  `xoa_ten_ma`. Tên đã học **không** dùng làm bằng chứng để mở khoá.
- Access ID/Secret cấu hình kiểu `bi_mat`, không bao giờ quay lại qua API,
  log hay `suc_khoe`.

## Việc cần quyết / CHƯA kiểm

- Chưa kiểm với khoá cửa thật: mã lệnh mở/đóng và mã DP trạng thái là theo
  từng mẫu khoá — kiểm thật trước khi bật `cho_phep_dieu_khien=bat`.
- `ma_dp_trang_thai` mặc định `lock_motor_state`, giá trị `true` được hiểu
  là đã khoá (quy ước khoá Tuya, chưa kiểm khoá thật).

## Tận dụng từ c2a

- `services/khoa_cua_nha.py` (d879b03): bảng cách mở `_CACH_MO`,
  `_ma()`/`_mo_ta_ma()`, logic học tên + trần hỏi `_HOI_TOI_DA`, đường nhật
  ký `door-lock/open-logs`, gộp trùng 5 giây, lọc «đóng cửa» khỏi sự kiện.
  Không port: nếp ngủ (cần lịch sử Home Assistant), vòng nhịp/heartbeat,
  thông báo qua `thong_bao`, sổ tên dùng chung.
- `services/tuya_nha.py` (d879b03): công thức ký HMAC-SHA256 + quy trình
  token (qua lớp `dam_may.py`, theo khuôn đã kiểm ở NV157).
