# Tự vận hành (tuvanhanh)

Bộ tự vận hành cho web2api: lập lịch nền làm việc tự động và hỏi chủ máy qua
**một đường kênh duy nhất** khi cần quyết định. Port từ c2a của chính chủ máy
(kho `TriTue2011/chatgpt2api`, nhánh main — đang chạy thật).

## Tệp đã chép từ c2a và chỉnh gì

| Tệp c2a | Chép thành | Chỉnh gì |
|---|---|---|
| `services/hoi_giam_sat.py` (175 dòng) | `hop_thu.py` | Bỏ ghi tệp JSON (giữ trong bộ nhớ theo vòng đời module); thêm hàng đợi có trần (c2a chỉ 1 câu hỏi); giữ nguyên tiền tố `cl `, hạn 6 giờ, không tự suy diễn |
| `services/web_session_scheduler.py` (225 dòng) | `lich.py` | `threading.Thread` → `asyncio.Task` (hợp vòng sự kiện web2api, không luồng mồ côi); đồng hồ/hàm ngủ tiêm được cho test; giữ nguyên lùi dần khi lỗi, single-flight |
| `services/solver_selfheal.py` (59 dòng) | logic trong `module.py` (`_nv_tu_khoi_phuc`) | Cooldown 5 phút → lùi dần theo lịch; quá N lần thì báo một tin rồi thôi |
| `services/account_recovery.py` (1764 dòng) | KHÔNG chép nguyên | Chỉ lấy ý tưởng: đăng nhập lại qua **hợp đồng đăng nhập chung** của module (`tai_dung_ho_so`), không điều khiển trình duyệt trực tiếp |

Kích thước: `hop_thu.py` ~4,9KB, `lich.py` ~3,8KB, `module.py` ~14KB.

## Cài đặt từng bước

1. Hệ thống → Module → tìm **Tự vận hành** → Cài đặt.
2. **Module kênh gửi** (`kenh_module`): id module kênh dùng để gửi báo/hỏi,
   ví dụ `telegram-bot`. Bắt buộc.
3. **Id đích gửi** (`kenh_id`): id đích trong `kenh_ra()` của module kênh,
   ví dụ `nha`. Bắt buộc — đích nêu đích danh, không gửi nhầm kênh.
4. Công tắc (mặc định đã đúng ý chủ máy 19/10):
   - Tự đăng nhập lại / Làm mới định kỳ / Theo dõi nguồn / Dọn dẹp đĩa: **BẬT**.
   - Tự cài module, Giải CAPTCHA, Tự đăng ký: **TẮT** (rủi ro, bật thủ công).
5. Lưu. Bộ lập lịch chạy ngay trong tiến trình web2api (không cần khởi động lại).

## Dùng

- Khi cần quyết định, module gửi tin đánh số qua kênh đã cấu hình, ví dụ:
  ```
  Có cài module X?
  1. Có
  2. Không
  Trả lời: nhắn "cl 1" (hoặc số tương ứng).
  ```
- Trả lời `cl 1` (chỉ tài khoản quản trị). Không trả lời trong 6 giờ → câu hỏi
  hết hạn, module **không làm gì** (mặc định an toàn).
- Xem trạng thái: công cụ chỉ-đọc `tu_van_hanh_trang_thai()`.

## Chưa kiểm

- Chưa kiểm với kênh thật (Telegram/Zalo thật) — mới kiểm bằng kênh giả.
- Chưa kiểm đăng nhập lại với provider thật — mới kiểm bằng provider giả.
- Chưa đo RAM/đĩa thật.
- Gắn app cho bộ lập lịch hiện dùng lazy capture (lưu từ lệnh `cl` đầu tiên)
  hoặc `dat_app(app)`; trước khi có app, các nhiệm vụ cần gọi module khác
  được bỏ qua an toàn (ghi log).
